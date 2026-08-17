"""
远程媒体抓取器。
负责抓取远程图片并返回结构化结果，减少 MessagePushManager 中的媒体获取职责。

支持：
- 按 URL 注入请求级 Headers（如防盗链 Referer），规避 CWA 等图片站的防盗链拦截；
- 对连接类异常 / HTTP 5xx / 超时做轻量指数退避重试，提升弱网下的抓取成功率。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


class RemoteMediaFetcher:
    """远程媒体抓取器。"""

    # 连接类异常名称标记：命中任一即视为可重试的瞬时故障。
    _RETRYABLE_EXCEPTION_MARKERS: tuple[str, ...] = (
        "TimeoutError",  # 连接/读取超时（含 aiohttp.ClientTimeoutError）
        "ConnectorError",  # 连接失败（DNS / 拒绝 / 重置）
        "ClientOSError",  # 操作系统级连接错误
        "Disconnected",  # 服务端断开连接
        "ConnectionResetError",
        "CannotConnect",
        "ServerDisconnectedError",
        "ProxyError",
    )

    def __init__(
        self,
        *,
        session_getter: Callable[[int | float | None], Awaitable[Any]],
        image_type_checker: Callable[[str | None], bool],
        content_type_guesser: Callable[[str | None], str | None],
        image_bytes_checker: Callable[[bytes | bytearray | memoryview | None], bool]
        | None = None,
        referer_builder: Callable[[str], str | None] | None = None,
        max_attempts: int = 3,
        retry_base_delay: float = 0.5,
    ):
        # 抓取器通过注入回调访问网络会话与内容类型判定能力，保持自身轻量。
        self._session_getter = session_getter  # 网络 session 异步获取回调
        self._image_type_checker = image_type_checker  # 图片 MIME 类型合法性校验回调
        self._content_type_guesser = (
            content_type_guesser  # URL 扩展名后缀 MIME 类型猜测回调
        )
        # 可选：按文件头校验真实图片，避免 MIME 伪装的 HTML 错误页被当成图片。
        self._image_bytes_checker = image_bytes_checker
        # 可选：按目标 URL 生成请求级 Headers（如防盗链 Referer）。
        self._referer_builder = referer_builder
        # 重试参数：总尝试次数（含首次）与指数退避基延迟。
        self._max_attempts = max(1, int(max_attempts))
        self._retry_base_delay = max(0.0, float(retry_base_delay))

    @staticmethod
    def _is_retryable_failure(result: dict[str, Any]) -> bool:
        """判断失败结果是否值得重试（连接类异常 / HTTP 5xx / 超时）。

        403/404 等 4xx 与「响应不是图片」类业务性失败不重试，
        避免无意义刷请求。
        """
        if result.get("data"):
            return False
        status = result.get("status")
        if isinstance(status, int) and 500 <= status < 600:
            return True
        exception_type = str(result.get("exception_type") or "")
        if any(
            marker in exception_type
            for marker in RemoteMediaFetcher._RETRYABLE_EXCEPTION_MARKERS
        ):
            return True
        error_msg = str(result.get("error") or "").lower()
        return "timeout" in error_msg or "timed out" in error_msg

    async def fetch(
        self,
        url: str,
        *,
        timeout_seconds: int | float | None = None,
        max_bytes: int = 10 * 1024 * 1024,
        expected_kind: str = "image",
    ) -> dict[str, Any]:
        """抓取远程媒体并返回结构化结果（带轻量重试）。"""
        normalized_url = url.strip()

        # 按目标 URL 生成请求级 Headers（如防盗链 Referer），
        # 由注入的回调决定是否携带，避免在共享 Session 上写死单一 Referer。
        request_headers: dict[str, str] | None = None
        if self._referer_builder is not None:
            try:
                referer = self._referer_builder(normalized_url)
            except Exception:
                referer = None
            if referer:
                request_headers = {"Referer": referer}

        for attempt in range(self._max_attempts):
            result = await self._fetch_once(
                normalized_url,
                request_headers=request_headers,
                timeout_seconds=timeout_seconds,
                max_bytes=max_bytes,
                expected_kind=expected_kind,
            )
            # 成功：直接返回
            if result.get("data"):
                return result
            # 最后一次尝试：返回最终失败结果
            if attempt >= self._max_attempts - 1:
                return result
            # 业务性失败（403/404/非图片等）不重试，立即返回
            if not self._is_retryable_failure(result):
                return result
            # 指数退避后重试
            await asyncio.sleep(self._retry_base_delay * (2**attempt))
        return result  # 防御性返回（正常不会走到）

    async def _fetch_once(
        self,
        normalized_url: str,
        *,
        request_headers: dict[str, str] | None,
        timeout_seconds: int | float | None,
        max_bytes: int,
        expected_kind: str,
    ) -> dict[str, Any]:
        """执行单次 HTTP GET 抓取并返回结构化结果。"""
        result: dict[str, Any] = {
            # 返回统一结构，便于上层日志与回退逻辑直接复用，无需感知 aiohttp 细节。
            "source_url": normalized_url,
            "final_url": normalized_url,
            "status": None,
            "content_type": None,
            "content_length": None,
            "bytes": None,
            "error": None,
            "exception_type": None,
        }

        try:
            session = await self._session_getter(
                timeout_seconds
            )  # 异步获取 ClientSession
            # 执行带有重定向跟踪的 HTTP GET 请求；
            # 请求级 Headers 与共享 Session Headers 合并，请求级优先。
            async with session.get(
                normalized_url, allow_redirects=True, headers=request_headers
            ) as response:
                # 把最终跳转地址、状态码与响应头统一记录下来，便于上层诊断抓取失败原因。
                result["status"] = response.status
                result["final_url"] = str(response.url)
                result["content_type"] = response.headers.get("Content-Type")
                content_length = response.headers.get("Content-Length")
                if content_length and content_length.isdigit():
                    result["content_length"] = int(content_length)
                    # 若服务端已声明体积超限，则直接提前返回，避免无意义下载。
                    if result["content_length"] > max_bytes:
                        result["error"] = (
                            f"响应体过大: {result['content_length']} bytes > {max_bytes} bytes"
                        )
                        return result

                # 仅处理 HTTP 200 成功的请求
                if response.status != 200:
                    result["error"] = f"HTTP {response.status}"
                    return result

                body = await response.read()  # 读取全部响应字节体
                result["bytes"] = len(body)
                # 再次校验下载后的文件实际大小
                if len(body) > max_bytes:
                    result["error"] = (
                        f"下载体过大: {len(body)} bytes > {max_bytes} bytes"
                    )
                    return result

                content_type = result["content_type"] or self._content_type_guesser(
                    result["final_url"]
                )
                result["content_type"] = content_type
                # 如果要求为图片，则使用回调校验 content-type 是否合法
                if expected_kind == "image" and not self._image_type_checker(
                    content_type
                ):
                    result["error"] = f"响应类型不是图片：{content_type or '未知类型'}"
                    return result

                # 进一步校验文件头，拦截 Content-Type 伪装成 image/* 的 HTML/JSON 错误页。
                if (
                    expected_kind == "image"
                    and self._image_bytes_checker is not None
                    and not self._image_bytes_checker(body)
                ):
                    preview = body[:48]
                    try:
                        preview_text = preview.decode("utf-8", errors="replace")
                    except Exception:
                        preview_text = repr(preview)
                    result["error"] = (
                        "响应体不是有效图片"
                        f"（内容类型为 {content_type or '未知'}，预览：{preview_text!r}）"
                    )
                    return result

                result["data"] = body  # 写入读取到的二进制数据
                return result
        except Exception as e:
            # 捕获连接超时、DNS 错误等全部异常，并将异常名和描述记录返回
            result["error"] = str(e)
            result["exception_type"] = type(e).__name__
            return result

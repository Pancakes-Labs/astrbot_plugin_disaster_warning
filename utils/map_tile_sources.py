"""
地图瓦片源URL配置
统一管理所有地图瓦片的URL模板
"""

import ipaddress
import re
from collections.abc import Iterable
from urllib.parse import urlsplit

# 中文名称到英文标识的映射
MAP_SOURCE_NAME_TO_ID = {
    "高德地图": "amap",
    "PetalMap矢量图亮": "petallight",
    "PetalMap矢量图暗": "petaldark",
    "ArcGIS卫星影像": "arcwi",
    "ArcGIS地形图": "arcwob",
    "ArcGIS山影图": "arcwh",
    "中科星图卫星影像": "geovis",
}

# 需要子域名轮询的地图源 —— 由 get_tile_subdomains() 提供候选列表。
# Leaflet 的 L.tileLayer 原生支持 {s} 占位符 + {subdomains: [...]} 选项：
# 它会为每个瓦片自动从列表中随机取一个子域替换 {s}，因此调用方无需任何额外处理。
MAP_SOURCE_SUBDOMAINS = {
    # 高德地图 webrd01 ~ webrd04
    "amap": ["1", "2", "3", "4"],
}

# 地图瓦片源URL映射
MAP_TILE_SOURCES = {
    # 高德地图（直接访问官方服务器，{s} 为 Leaflet 原生子域名占位符）
    "amap": "https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=7&x={x}&y={y}&z={z}",
    # PetalMap 矢量图（FAN Studio 瓦片代理）
    # 重要：FAN Studio(2026-08-22 01时左右)已将瓦片坐标从旧式 z/y/x 改为标准 z/x/y(Web Mercator XYZ)。
    # 若沿用 {z}/{y}/{x} 会将 x/y 互换，导致取到错误地理位置瓦片（整片陆地/海洋/破碎错位地形）。
    "petallight": "https://tilemap.fanstudio.tech/petallight/{z}/{x}/{y}",  # PetalMap 矢量图 亮
    "petaldark": "https://tilemap.fanstudio.tech/petaldark/{z}/{x}/{y}",  # PetalMap 矢量图 暗
    # ArcGIS 系列（FAN Studio 瓦片代理；同为标准 z/x/y）
    "arcwi": "https://tilemap.fanstudio.tech/arcwi/{z}/{x}/{y}",  # ArcGIS 卫星影像
    "arcwob": "https://tilemap.fanstudio.tech/arcwob/{z}/{x}/{y}",  # ArcGIS 地形图
    "arcwh": "https://tilemap.fanstudio.tech/arcwh/{z}/{x}/{y}",  # ArcGIS 山影图
    # 中科星图（FAN Studio 瓦片代理；上游已下架，返回"未找到名为 geovis 的地图源"）
    # 保留列表项以便配置兼容，但被标记为不可用；通过 fallback 兜底到可用的源。
    "geovis": "https://tilemap.fanstudio.tech/geovis/{z}/{x}/{y}",  # 中科星图 卫星影像（上游已失效，勿选）
}


# 已失效/不可用的地图源（上游已下架或长期不可用）。
# 命中这些源时，get_tile_url / get_tile_url_js 会兜底回退到默认源。
# 保留在列表中仅用于配置兼容，避免用户配置历史值被强制重置。
UNAVAILABLE_SOURCES = {
    "geovis",
}


def normalize_map_source(map_source: str) -> str:
    """
    将中文地图源名称转换为英文标识
    如果输入已经是英文标识，则直接返回

    Args:
        map_source: 地图源名称（中文或英文）

    Returns:
        英文标识符
    """
    # 如果是中文名称，转换为英文标识
    if map_source in MAP_SOURCE_NAME_TO_ID:
        return MAP_SOURCE_NAME_TO_ID[map_source]
    # 否则假定已经是英文标识，直接返回
    return map_source


def get_tile_url(map_source: str) -> str:
    """
    获取指定地图源的瓦片URL模板

    Args:
        map_source: 地图源标识符（中文名称或英文标识）

    Returns:
        瓦片URL模板字符串，如果未找到则返回默认的 petallight
    """
    source_id = normalize_map_source(map_source)
    if source_id in UNAVAILABLE_SOURCES:
        return MAP_TILE_SOURCES["petallight"]
    return MAP_TILE_SOURCES.get(source_id, MAP_TILE_SOURCES["petallight"])


def get_tile_subdomains(map_source: str) -> list[str]:
    """
    获取指定地图源的子域名候选列表（用于 Leaflet 的 subdomains 选项）。

    Args:
        map_source: 地图源标识符（中文名称或英文标识）

    Returns:
        子域名字符串列表；无子域名需求的源返回空列表
    """
    source_id = normalize_map_source(map_source)
    if source_id in UNAVAILABLE_SOURCES:
        return []
    return list(MAP_SOURCE_SUBDOMAINS.get(source_id, []))


def get_tile_url_js(map_source: str) -> str:
    """
    为 JavaScript 生成瓦片 URL 模板。

    Args:
        map_source: 地图源标识符（中文名称或英文标识）

    Returns:
        适用于 Leaflet 的 URL 模板字符串（沿用 {s}/{x}/{y}/{z} 占位符）
    """
    return get_tile_url(map_source)


# ── 代理绕过（proxy bypass）──────────────────────────────────────────────
# Playwright 启动的 Chromium 默认继承 AstrBot 进程的环境变量。当进程带有
# ALL_PROXY=socks5://... / HTTPS_PROXY=http://... 这类代理设置时（容器、systemd
# 单元、sing-box / clash 注入小写变量都会造成这种情况），Chromium 会把这些地图
# 瓦片请求一并发往代理。若代理无法正确转发这些域名，就会返回
# net::ERR_EMPTY_RESPONSE，导致地图底图整体空白而卡片其他部分正常。
#
# 因此需要让地图瓦片域名绕过代理直连。有两个必须同时处理的点：
# 1. Chromium 的 --proxy-bypass-list 启动参数与 NO_PROXY 环境变量走的是不同的
#    判定路径，只设其一无法覆盖所有版本/场景，两者都要设置。
# 2. Chromium 读取 no_proxy 时小写变量优先于大写，仅设置 NO_PROXY 往往不生效，
#    因此大小写两份都必须写入。
MAP_TILE_BYPASS_DOMAINS = {
    # 高德：瓦片走 webrd0N.is.autonavi.com，同时放行 amap.com 便于后续扩展
    "amap": ["*.autonavi.com", "*.amap.com"],
}

# 默认绕过域名：覆盖全部内置瓦片源域名，语义为「地图瓦片始终直连」。
# 这样即使用户之后切换地图源（或某源临时失效需要兜底），也无需重新配置。
DEFAULT_PROXY_BYPASS_DOMAINS: tuple[str, ...] = (
    "*.autonavi.com",
    "*.amap.com",
    # FAN Studio 瓦片代理（PetalMap / ArcGIS 系列共用）
    "*.fanstudio.tech",
)

# Chromium --proxy-bypass-list 以分号分隔。
PROXY_BYPASS_LIST_SEPARATOR = ";"

_NO_PROXY_ENV_KEYS: tuple[str, ...] = ("NO_PROXY", "no_proxy")


def _is_ip_literal(host: str) -> bool:
    """判断主机是否为合法 IP 字面量。"""
    candidate = host.strip()
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    if not candidate:
        return False
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return True


def _extract_host(token: str) -> str:
    """从用户输入的一项规则中提取主机部分。

    兼容用户直接粘贴完整 URL、带端口与带路径的写法；
    IPv6 字面量的方括号会被保留，避免被端口分隔符截断
    """
    raw = str(token or "").strip()
    if not raw:
        return ""
    # 含 scheme 或前导 //：交给 urlsplit 提取 hostname
    if "://" in raw or raw.startswith("//"):
        parsed = urlsplit(raw if "://" in raw else f"//{raw}")
        hostname = parsed.hostname or ""
        if not hostname:
            return ""
        # hostname 会剥离 IPv6 方括号，补回以保持可辨识
        return f"[{hostname}]" if ":" in hostname else hostname
    # 无 scheme：手工剥离路径 / 查询 / 端口
    host = re.split(r"[/?#]", raw, maxsplit=1)[0]
    # 带方括号的 IPv6：截取到匹配的右括号，忽略其后的端口
    if host.startswith("["):
        end = host.find("]")
        return host[: end + 1] if end > 0 else host
    if ":" in host:
        host = host.split(":", 1)[0]
    return host


def get_tile_bypass_domains(map_source: str) -> list[str]:
    """
    获取指定地图源建议的代理绕过域名（不含默认兜底项）。

    Args:
        map_source: 地图源标识符（中文名称或英文标识）

    Returns:
        该源专属的绕过域名列表；无需专属项时返回空列表
    """
    source_id = normalize_map_source(map_source)
    return list(MAP_TILE_BYPASS_DOMAINS.get(source_id, []))


def _normalize_bypass_tokens(token: str) -> list[str]:
    """把用户输入的一项绕过规则规范化为 Chromium 可识别的域名规则列表。"""
    host = _extract_host(token)
    if not host:
        return []
    lowered = host.lower()
    # 已是通配符形式
    if lowered.startswith("*."):
        base = lowered[2:]
        return [lowered] if base else []
    # 前导点形式 .example.com -> *.example.com（前导点语义即"仅子域"）
    if lowered.startswith("."):
        base = lowered[1:]
        return [f"*.{base}"] if base else []
    # IP 字面量与单标签主机（如 localhost）只需精确匹配
    if _is_ip_literal(lowered) or "." not in lowered:
        return [lowered]
    # 普通域名：精确主机 + 子域通配，二者都要，缺一不可
    return [lowered, f"*.{lowered}"]


def normalize_proxy_bypass_domains(
    extra: str | Iterable[str] | None,
    *,
    include_defaults: bool = True,
) -> list[str]:
    """
    合并「默认地图域名」与「用户额外配置」，去重并保持顺序。

    Args:
        extra: 用户配置的额外域名，支持逗号/分号/空白分隔的字符串，
            或已拆分好的字符串可迭代对象。
        include_defaults: 是否把 DEFAULT_PROXY_BYPASS_DOMAINS 置于结果最前。

    Returns:
        规范化后的绕过域名列表
    """
    tokens: list[str] = []
    if include_defaults:
        tokens.extend(DEFAULT_PROXY_BYPASS_DOMAINS)
    if isinstance(extra, str):
        tokens.extend(re.split(r"[,;\s]+", extra))
    elif extra is not None:
        for item in extra:
            if isinstance(item, str):
                tokens.extend(re.split(r"[,;\s]+", item))

    result: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        # 单项规则可能展开为多条（精确主机 + 子域通配）
        for normalized in _normalize_bypass_tokens(token):
            if normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
    return result


def build_proxy_bypass_list_arg(domains: Iterable[str]) -> str:
    """生成 Chromium ``--proxy-bypass-list`` 的参数值（分号分隔）。"""
    return PROXY_BYPASS_LIST_SEPARATOR.join(
        str(domain).strip() for domain in domains if str(domain or "").strip()
    )


def merge_no_proxy_into_env(
    env: dict[str, str],
    domains: Iterable[str],
) -> dict[str, str]:
    """
    把绕过域名分别追加进 NO_PROXY 与 no_proxy（两个变量互不搬运已有值）。

    每个变量都以自身原有规则为基准，仅追加本功能的地图瓦片域名。

    Args:
        env: 目标环境变量字典（通常为 dict(os.environ) 的拷贝），原地修改。
        domains: 需要绕过代理的域名列表。

    Returns:
        同一个 env 对象，便于链式使用
    """
    additions = [
        str(domain or "").strip() for domain in domains if str(domain or "").strip()
    ]
    for key in _NO_PROXY_ENV_KEYS:
        merged: list[str] = []
        seen: set[str] = set()
        # 基准：仅取该变量自身已有的值
        for item in re.split(r"[,;\s]+", str(env.get(key, "") or "")):
            token = item.strip()
            if token and token not in seen:
                seen.add(token)
                merged.append(token)
        for token in additions:
            if token not in seen:
                seen.add(token)
                merged.append(token)
        env[key] = ",".join(merged)
    return env

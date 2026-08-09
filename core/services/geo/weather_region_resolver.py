"""
气象地区解析服务。
负责从标题、头条和外部行政区划接口中推断气象预警所属省份。

行政区划回退查询使用 ResAPI（https://www.resapi.cn）：
- 搜索接口：GET /v1/regions/search?q=地名
  返回条目含 full_name（如"甘肃省/陇南市/康县"），首段即省级名称，
  无需再调用 ancestors 接口即可得到省份归属。
- 原民政部 dmfw.mca.gov.cn 接口已失效（403 Forbidden），不再使用。
"""

from __future__ import annotations

import asyncio
import re
import time

import aiohttp

from astrbot.api import logger

# 中国 34 个省级行政区划简称与全名关键字定义列表
CHINA_PROVINCES = [
    "北京",
    "天津",
    "上海",
    "重庆",
    "河北",
    "山西",
    "辽宁",
    "吉林",
    "黑龙江",
    "江苏",
    "浙江",
    "安徽",
    "福建",
    "江西",
    "山东",
    "河南",
    "湖北",
    "湖南",
    "广东",
    "海南",
    "四川",
    "贵州",
    "云南",
    "陕西",
    "甘肃",
    "青海",
    "台湾",
    "内蒙古",
    "广西",
    "西藏",
    "宁夏",
    "新疆",
    "香港",
    "澳门",
]

# ResAPI 行政区划搜索接口地址
_RESAPI_REGIONS_SEARCH_URL = "https://www.resapi.cn/v1/regions/search"

# 地名别名映射：气象预警常用功能/惯用名 → 官方行政区划名。
# 平潭综合实验区在 2026 全国区划中仍为"平潭县"；
# 洋浦经济开发区行政上隶属儋州市（460400），ResAPI 无"洋浦"独立实体。
# 后续遇到类似"功能区名 ≠ 官方区划名"的地名可在此补充。
_PLACE_ALIASES: dict[str, str] = {
    "平潭综合实验区": "平潭县",
    "洋浦": "儋州市",
    "洋浦经济开发区": "儋州市",
    "两江新区": "重庆市",
}

# 功能区尾缀：查询失败时截掉这些尾缀做受控退化查询。
# 退化后的查询词仍需通过省份唯一校验，防止命中跨省同名乡镇村等噪音。
_FUNCTIONAL_ZONE_SUFFIXES = (
    "综合实验区",
    "实验区",
    "经济技术开发区",
    "高新技术产业开发区",
    "高新区",
    "新区",
    "开发区",
    "特区",
    "风景名胜区",
    "风景区",
    "景区",
)

# 行政区划后缀（用于从 headline 中剥离市县级地名）。
# 覆盖省直辖县/旗、自治州/县、地级市、市辖区、县级市、特区、林区、新区等。
_PLACE_SUFFIXES = (
    "特别行政区",
    "自治州",
    "自治县",
    "自治旗",
    "民族乡",
    "风景名胜区",
    "新区",
    "林区",
    "地区",
    "盟",
    "市",
    "区",
    "县",
    "旗",
)

# 地名提取主正则：非贪婪截取以行政区划后缀结尾的连续汉字段。
# 长度 2~30，防止把整条"XX市气象台发布..."吞成超长地名。
_RE_PLACE = re.compile(
    r"([\u4e00-\u9fa5]{2,30}?(?:" + "|".join(_PLACE_SUFFIXES) + r"))"
)

# 兜底正则：匹配"XX气象局/气象台/气象站"前的机构名（去掉尾缀后作为备选地名）。
_RE_ORG_TAIL = re.compile(r"([\u4e00-\u9fa5]{2,30}?)气象(?:局|台|站|中心)")

# 地名中的无意义修饰词，命中即视为噪声候选
_NOISE_PATTERNS = [
    re.compile(r"气象(?:局|台|站|中心)"),
    re.compile(r"发布"),
    re.compile(r"更新"),
    re.compile(r"预警"),
]


class WeatherRegionResolver:
    """气象预警地区解析器。

    负责综合标题文本、本地规则与外部区划查询结果来确定省份归属。
    """

    def __init__(self):
        # 缓存行政地名反查省份成功与失败的结果字典，避免过度发起外部 HTTP 请求
        self._location_province_cache: dict[str, str | None] = {}
        # 缓存地名过期时间戳字典
        self._cache_expire: dict[str, float] = {}
        # 失败请求的缓存有效期限制（秒），防止瞬时重试打满带宽
        self._failure_ttl = 60.0
        # 内部重用的 HTTP ClientSession 客户端会话
        self._session: aiohttp.ClientSession | None = None

    def extract_province(self, title_text: str) -> str | None:
        """直接从标题中提取省级行政区名称。"""
        # 简单快速的字符串子串判定
        for province in CHINA_PROVINCES:
            if province in title_text:
                return province
        return None

    def _normalize_province_name(self, province_name: str) -> str | None:
        """把外部接口返回的省名归一化为标准名称。"""
        normalized = province_name.strip()
        if not normalized:
            return None
        # 查找标准列表中匹配的简称
        for province in CHINA_PROVINCES:
            if province in normalized:
                return province
        return None

    def _is_noise_place(self, place: str) -> bool:
        """判断候选地名是否属于无意义的噪声片段。"""
        if not place:
            return True
        # 命中"气象台/发布/更新/预警"等关键词视为噪声
        if any(pattern.search(place) for pattern in _NOISE_PATTERNS):
            return True
        # 纯方位/量词等极短无意义片段
        if re.fullmatch(
            r"(?:东部|西部|南部|北部|中部|局部|大部|部分|上游|下游)", place
        ):
            return True
        return False

    def _extract_place_from_headline(self, headline_text: str) -> str | None:
        """从头条文本中尽量提取市县级地名。

        优先使用行政区划后缀正则抽取（非贪婪），
        失败时回退到"气象局/气象台"前的机构名。
        """
        if not headline_text:
            return None

        # 第一步：行政区划后缀正则抽取，跳过噪声片段
        for place in _RE_PLACE.findall(headline_text):
            if self._is_noise_place(place):
                continue
            # 跳过含省级名称的宽泛匹配（如"河北省石家庄市"），
            # 优先更细的区县名（如"新华区"），避免带省份前缀导致外部查询失败
            if self.extract_province(place):
                continue
            return place

        # 第二步：兜底截取"XX气象局/气象台"前的机构名作为备选地名
        org_match = _RE_ORG_TAIL.search(headline_text)
        if org_match:
            org_name = org_match.group(1)
            if not self._is_noise_place(org_name):
                return org_name

        # 第三步：极端兜底，截取"气象站/气象台"前的整段汉字
        fallback_text = re.split(r"气象(?:站|台)", headline_text, maxsplit=1)[0].strip()
        if fallback_text:
            fallback_text = re.sub(r"^[^\u4e00-\u9fa5]+", "", fallback_text)
            fallback_text = re.sub(r"[^\u4e00-\u9fa5]+$", "", fallback_text)
            if fallback_text and not self._is_noise_place(fallback_text):
                return fallback_text
        return None

    def _get_session(self) -> aiohttp.ClientSession:
        """获取内部复用的网络会话。"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"})
        return self._session

    async def close(self) -> None:
        """关闭内部网络会话。"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _build_query_candidates(self, place_name: str) -> list[str]:
        """生成地名查询候选队列（原词 → 别名 → 退化尾缀）。

        用于处理"功能/惯用名 ≠ 官方行政区划名"的场景，
        例如"平潭综合实验区"在官方区划中仍是"平潭县"。
        """
        candidates: list[str] = []
        seen: set[str] = set()

        def add(candidate: str) -> None:
            candidate = candidate.strip()
            if candidate and candidate not in seen:
                seen.add(candidate)
                candidates.append(candidate)

        # 1. 原词优先
        add(place_name)
        # 2. 别名映射（手工维护，如"平潭综合实验区"→"平潭县"）
        alias = _PLACE_ALIASES.get(place_name)
        if alias:
            add(alias)
        # 3. 截掉功能区尾缀做受控退化（如"XX综合实验区"→"XX"）
        for suffix in _FUNCTIONAL_ZONE_SUFFIXES:
            if place_name.endswith(suffix):
                add(place_name[: -len(suffix)])
                break
        return candidates

    async def _query_province_by_place_name(self, place_name: str) -> str | None:
        """通过 ResAPI 行政区划搜索接口按地名反查所属省份。

        依次尝试原词、别名映射、退化尾缀候选；任一候选命中唯一省份即返回。
        """
        now = time.monotonic()
        # 成功结果直接缓存，失败结果短时缓存，减少重复网络查询。
        if place_name in self._location_province_cache:
            cached = self._location_province_cache[place_name]
            if cached is not None:
                return cached
            if now < self._cache_expire.get(place_name, 0):
                return None

        for query in self._build_query_candidates(place_name):
            province = await self._resolve_province_from_search(query)
            if province is not None:
                self._location_province_cache[place_name] = province
                return province

        self._location_province_cache[place_name] = None
        self._cache_expire[place_name] = now + self._failure_ttl
        return None

    async def _resolve_province_from_search(self, query: str) -> str | None:
        """对单个查询词执行 ResAPI 搜索并解析省份。

        匹配策略：
        1. 仅接受 name 与查询词完全相同的精确命中，或以查询词开头的合理扩展命中
           （如"五台山"→"五台山风景名胜区"），避免把模糊结果误判为精确命中；
        2. 命中记录不限层级（含 town/village），允许功能区退化后只剩乡镇级的场景参与省份判定；
        3. 所有命中记录的省份集合唯一时才采纳（核心安全阀），
           同名跨省/模糊命中多省时返回 None，不猜测；
        4. 从命中记录的 full_name 首段解析省份。
        5. 遍历所有结果页收集完整匹配集合（最多 _RESAPI_MAX_PAGES 页），
           避免同名/同前缀记录分布在后续页时省份唯一校验只看到第一页而误判。
        """
        # 分页遍历上限：防止极端情况下接口返回大量页拖慢统计链路。
        max_pages = 3
        page_size = 10
        matched_records: list[dict] = []
        last_error: Exception | None = None

        # 网络层偶发故障（连接超时/断开等）时重试一次，避免整条统计丢失。
        # 分页内最多尝试 2 次；某页连续失败则放弃后续页（已有匹配仍可参与判定）。
        for page in range(1, max_pages + 1):
            params = {
                "q": query,
                "page": str(page),
                "page_size": str(page_size),
            }
            payload = None
            for attempt in range(2):
                try:
                    session = self._get_session()
                    async with session.get(
                        _RESAPI_REGIONS_SEARCH_URL,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        if resp.status != 200:
                            logger.debug(
                                f"[灾害预警] ResAPI 行政区划查询 HTTP {resp.status}，"
                                f"查询词为 {query}"
                            )
                            continue
                        payload = await resp.json(content_type=None)
                        break
                except Exception as exc:
                    # 注意：aiohttp 的 ClientConnectionError / asyncio.TimeoutError
                    # 等异常 str(exc) 为空，仅打 f"{exc}" 无法区分错误类型。
                    # 这里补打异常类型名与完整信息（含 errno 等），便于排障。
                    logger.error(
                        f"[灾害预警] 行政区划查询失败(第 {page} 页第 {attempt + 1}/2 次)，"
                        f"查询词为 {query}，异常类型为 {type(exc).__name__}，"
                        f"错误信息为 {exc!r}"
                    )
                    last_error = exc
                    if attempt == 0:
                        await asyncio.sleep(0.3)
            if payload is None:
                # 当前页失败：若已有匹配记录则停止分页（避免重复请求），否则整次失败。
                if matched_records:
                    break
                break

            # 顶层类型校验：ResAPI 正常返回 dict，但网关错误/错误页等场景
            # 可能解析出数组、字符串或标量；此时 payload.get 会抛 AttributeError，
            # 而该异常位于 try 块之外，会逃逸到历史统计重建路径导致中断。
            if not isinstance(payload, dict):
                logger.debug(
                    f"[灾害预警] ResAPI 行政区划响应顶层类型异常 "
                    f"({type(payload).__name__})，查询词为 {query}"
                )
                break

            records = payload.get("data") or []
            if not isinstance(records, list):
                records = []

            page_matched = 0
            for record in records:
                if not isinstance(record, dict):
                    continue
                name = str(record.get("name") or "").strip()
                full_name = str(record.get("full_name") or "")
                if not full_name:
                    continue
                # 精确命中或名称以查询词开头的扩展命中均纳入候选；
                # 不再限制省市区级，但最终省份必须唯一（见下方 province_set 校验）。
                if name == query or name.startswith(query):
                    matched_records.append(record)
                    page_matched += 1

            # 当前页未产生任何命中，说明后续页也不会有更多相关记录，提前终止分页。
            if page_matched == 0:
                break

        del last_error

        if matched_records:
            # 取命中记录的省份集合，归一化后去重
            province_set: set[str] = set()
            for record in matched_records:
                full_name = str(record.get("full_name") or "")
                # full_name 形如"甘肃省/陇南市/康县"，首段即省级名称
                province_part = full_name.split("/", 1)[0].strip()
                province = self._normalize_province_name(province_part)
                if province:
                    province_set.add(province)

            # 命中记录归一化后得到唯一省份时才采纳，避免同名跨省时任意猜测
            if len(province_set) == 1:
                return province_set.pop()

        return None

    async def extract_province_with_fallback(
        self, title_text: str, headline_text: str = ""
    ) -> str | None:
        """按“标题直取 -> 头条直取 -> 头条提取 -> 外部查询”顺序解析省份。"""
        # 第一阶段：尝试从标题文本直接提取
        province = self.extract_province(title_text)
        if province is not None:
            return province
        # 第二阶段：头条文本可能带省份前缀，先直接尝试提取省份
        province = self.extract_province(headline_text)
        if province is not None:
            return province
        # 第三阶段：提取更小的地名段，并发起 ResAPI 地名搜索
        place_name = self._extract_place_from_headline(headline_text)
        if not place_name:
            return None
        return await self._query_province_by_place_name(place_name)

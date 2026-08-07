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

    async def _query_province_by_place_name(self, place_name: str) -> str | None:
        """通过 ResAPI 行政区划搜索接口按地名反查所属省份。"""
        now = time.monotonic()
        # 成功结果直接缓存，失败结果短时缓存，减少重复网络查询。
        if place_name in self._location_province_cache:
            cached = self._location_province_cache[place_name]
            if cached is not None:
                return cached
            if now < self._cache_expire.get(place_name, 0):
                return None

        # ResAPI 搜索接口参数
        params = {
            "q": place_name,
            "page": "1",
            "page_size": "10",
        }
        try:
            session = self._get_session()
            async with session.get(
                _RESAPI_REGIONS_SEARCH_URL,
                params=params,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    logger.debug(
                        f"[灾害预警] ResAPI 行政区划查询 HTTP {resp.status}，"
                        f"地点为 {place_name}"
                    )
                    self._location_province_cache[place_name] = None
                    self._cache_expire[place_name] = now + self._failure_ttl
                    return None
                payload = await resp.json(content_type=None)
        except Exception as exc:
            logger.debug(
                f"[灾害预警] 行政区划查询失败，地点为 {place_name}，错误为 {exc}"
            )
            self._location_province_cache[place_name] = None
            self._cache_expire[place_name] = now + self._failure_ttl
            return None

        records = payload.get("data") or []
        if not isinstance(records, list):
            records = []

        # 匹配策略：
        # 1. 优先"名称与查询词完全相同"的精确命中；
        # 2. 同精确命中中，优先 district（区县级）记录，避免命中乡镇村等低层级；
        # 3. 从命中记录的 full_name 首段解析省份。
        def score(record: dict) -> int:
            name = str(record.get("name") or "")
            level = str(record.get("level") or "")
            # 名称精确匹配加分最高
            exact_bonus = 100 if name == place_name else 0
            # 层级优先级：district(区县)=60, city(市)=40, province(省)=20,
            # town/village 等更低层级不额外加分
            level_bonus = {
                "district": 60,
                "city": 40,
                "province": 20,
                "town": 0,
                "village": 0,
            }.get(level, 10)
            return exact_bonus + level_bonus

        best_record: dict | None = None
        best_score = -1
        for record in records:
            full_name = str(record.get("full_name") or "")
            if not full_name:
                continue
            s = score(record)
            if s > best_score:
                best_score = s
                best_record = record

        if best_record is not None:
            full_name = str(best_record.get("full_name") or "")
            # full_name 形如"甘肃省/陇南市/康县"，首段即省级名称
            province_part = full_name.split("/", 1)[0].strip()
            province = self._normalize_province_name(province_part)
            if province:
                self._location_province_cache[place_name] = province
                return province

        self._location_province_cache[place_name] = None
        self._cache_expire[place_name] = now + self._failure_ttl
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

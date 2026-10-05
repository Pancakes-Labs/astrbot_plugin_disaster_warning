"""
气象地区解析服务。
负责从标题、头条、行政区划代码（adcode）与本地行政区划资源库中推断气象预警所属省份。

底层数据基于 resources/china_regions.json 本地行政区划字典（GB/T 2260 权威数据），
纯本地极速计算，无需依赖外部网络接口，避免外部接口失效或超时导致统计丢失。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from astrbot.api import logger

from ....utils.china_regions import (
    extract_province_from_adcode,
    province_short,
    resolve_province_from_text,
)

# 本地行政区划资源文件相对路径
_REGIONS_JSON_REL_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "resources", "china_regions.json"
)

# 地名别名映射：气象预警常用功能/惯用名 → 官方行政区划名
_PLACE_ALIASES: dict[str, str] = {
    "平潭综合实验区": "平潭县",
    "平潭": "平潭县",
    "洋浦": "儋州市",
    "洋浦经济开发区": "儋州市",
    "两江新区": "重庆市",
    "大通湖区": "大通湖管理区",
    "庐山景区": "庐山市",
    "天府新区": "成都市",
    "雄安新区": "保定市",
    "西咸新区": "西安市",
    "沈抚新区": "沈阳市",
    "金普新区": "大连市",
    "贵安新区": "贵阳市",
    "赣江新区": "南昌市",
    "长春新区": "长春市",
    "哈尔滨新区": "哈尔滨市",
    "舟山群岛新区": "舟山市",
    "青岛西海岸新区": "青岛市",
    "滇中新区": "昆明市",
    "福州新区": "福州市",
}

# 功能区尾缀：查询失败时截掉这些尾缀做受控退化查询
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
    "管理区",
)

# 行政区划后缀（用于从 headline 中剥离市县级地名）
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

# 地名提取主正则：非贪婪截取以行政区划后缀结尾的连续汉字段
_RE_PLACE = re.compile(
    r"([\u4e00-\u9fa5]{2,30}?(?:" + "|".join(_PLACE_SUFFIXES) + r"))"
)

# 兜底正则：匹配"XX气象局/气象台/气象站"前的机构名（去掉尾缀后作为备选地名）
_RE_ORG_TAIL = re.compile(r"([\u4e00-\u9fa5]{2,30}?)气象(?:局|台|站|中心)")

# 地名中的无意义修饰词，命中即视为噪声候选
_NOISE_PATTERNS = [
    re.compile(r"气象(?:局|台|站|中心)"),
    re.compile(r"局"),
    re.compile(r"与"),
    re.compile(r"发布"),
    re.compile(r"更新"),
    re.compile(r"预警"),
]


class _LocalChinaRegionsDb:
    """本地中国行政区划字典加载器（单例模式）。"""

    _instance: _LocalChinaRegionsDb | None = None

    def __init__(self):
        self.code_map: dict[str, list[Any]] = {}
        self.place_to_province: dict[str, str] = {}
        self._loaded = False
        self._load()

    @classmethod
    def get_instance(cls) -> _LocalChinaRegionsDb:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load(self) -> None:
        if self._loaded:
            return
        abs_path = os.path.abspath(_REGIONS_JSON_REL_PATH)
        if not os.path.exists(abs_path):
            logger.warning(f"[灾害预警] 未找到本地行政区划资源文件: {abs_path}")
            return

        try:
            with open(abs_path, encoding="utf-8") as f:
                data = json.load(f)

            self.code_map = data.get("codeMap", {})

            # 预收集全部省级名称（全称 + 简称）。
            # 必须独立于下面的遍历顺序：市/区/县剥离后缀后可能与省级名称同名
            # （天津市「河北区」→「河北」、内蒙古「海南区」→「海南」），
            # 若不保护，它们会被当作跨省冲突剔除，导致省级简称的反查条目在加载时被静默删除。
            province_level_names: set[str] = set()
            for item in self.code_map.values():
                if not item or len(item) < 3:
                    continue
                prov = str(item[0] or "").strip()
                if not prov:
                    continue
                province_level_names.add(prov)
                p_short = province_short(prov)
                if p_short:
                    province_level_names.add(p_short)

            # 建立地名（市、区、县）到省份的反查字典
            # 记录冲突的地名（全国同名的区县如“新华区”、“朝阳区”等，跨省冲突时不武断判定）
            conflict_places: set[str] = set()

            for code, item in self.code_map.items():
                if not item or len(item) < 3:
                    continue
                prov = str(item[0] or "").strip()
                if not prov:
                    continue
                city = str(item[1] or "").strip() if item[1] else None
                district = str(item[2] or "").strip() if item[2] else None

                # 记录省级全称与简称
                self.place_to_province[prov] = prov
                p_short = province_short(prov)
                if p_short:
                    self.place_to_province[p_short] = prov

                for place in (city, district):
                    if not place:
                        continue
                    # 与省级名称同名的市/区/县（如直辖市行）不参与市县级映射
                    if place in province_level_names:
                        continue
                    if (
                        place in self.place_to_province
                        and self.place_to_province[place] != prov
                    ):
                        conflict_places.add(place)
                    else:
                        self.place_to_province[place] = prov

                    # 剥离"市/区/县/旗/盟/州"后缀作为次级检索项
                    for sfx in ("市", "区", "县", "旗", "盟", "州"):
                        if place.endswith(sfx) and len(place) > 2:
                            base_name = place[: -len(sfx)]
                            # 剥离后与省级名称同名时保留省级条目（如「河北区」→「河北」）
                            if base_name in province_level_names:
                                continue
                            if (
                                base_name in self.place_to_province
                                and self.place_to_province[base_name] != prov
                            ):
                                conflict_places.add(base_name)
                            else:
                                self.place_to_province[base_name] = prov

            # 剔除跨省冲突地名，避免同名误判
            for c in conflict_places:
                self.place_to_province.pop(c, None)

            # 注入手工维护的别名映射
            for alias, official in _PLACE_ALIASES.items():
                if official in self.place_to_province:
                    self.place_to_province[alias] = self.place_to_province[official]

            self._loaded = True
            logger.debug(
                f"[灾害预警] 成功加载本地行政区划字典: {len(self.code_map)} 个区划代码，"
                f"{len(self.place_to_province)} 个本地地名映射"
            )
        except Exception as exc:
            logger.error(f"[灾害预警] 加载本地行政区划字典失败: {exc}")

    def query_by_adcode(self, adcode_or_id: str | None) -> str | None:
        """通过 adcode 或预警 ID 查询省级行政区。"""
        if not adcode_or_id:
            return None
        code_str = str(adcode_or_id).strip()
        if not code_str:
            return None

        # 优先在 code_map 中精准匹配
        # 1. 尝试提取 6 位 adcode
        m6 = re.match(r"^(\d{6})", code_str)
        if m6:
            c6 = m6.group(1)
            item = self.code_map.get(c6)
            if item and item[0]:
                return province_short(item[0])

        # 2. 尝试提取 2 位省级代码
        m2 = re.match(r"^(\d{2})", code_str)
        if m2:
            c2 = m2.group(1)
            item = self.code_map.get(c2)
            if item and item[0]:
                return province_short(item[0])

        return extract_province_from_adcode(code_str)

    def query_by_place_name(self, place_name: str) -> str | None:
        """通过地名在本地索引中反查所属省份简称。"""
        if not place_name:
            return None
        name = place_name.strip()
        if not name:
            return None

        prov = self.place_to_province.get(name)
        if prov:
            return province_short(prov)

        # 尝试别名映射
        alias = _PLACE_ALIASES.get(name)
        if alias and alias in self.place_to_province:
            return province_short(self.place_to_province[alias])

        # 尝试剥离功能区尾缀做受控退化
        for sfx in _FUNCTIONAL_ZONE_SUFFIXES:
            if name.endswith(sfx) and len(name) > len(sfx):
                base = name[: -len(sfx)]
                prov = self.place_to_province.get(base)
                if prov:
                    return province_short(prov)

        return None


class WeatherRegionResolver:
    """气象预警地区解析器。

    负责综合标题文本、发布头条、行政区划代码（adcode）与本地行政区划库来确定省份归属。
    所有解析过程完全在本地完成，零网络调用。
    """

    def __init__(self):
        self._db = _LocalChinaRegionsDb.get_instance()

    def extract_province(self, title_text: str) -> str | None:
        """直接从标题中提取省级行政区简称。"""
        if not title_text:
            return None
        # 1. 优先使用文本全称/简称/全国级规则解析
        prov = resolve_province_from_text(title_text)
        if prov:
            return prov

        # 2. 从标题中提取地名，并在本地地名库中反查
        place = self._extract_place_from_headline(title_text)
        if place:
            prov = self._db.query_by_place_name(place)
            if prov:
                return prov

        return None

    def _is_noise_place(self, place: str) -> bool:
        """判断候选地名是否属于无意义的噪声片段。"""
        if not place:
            return True
        if any(pattern.search(place) for pattern in _NOISE_PATTERNS):
            return True
        if re.fullmatch(
            r"(?:东部|西部|南部|北部|中部|局部|大部|部分|上游|下游)", place
        ):
            return True
        return False

    def _extract_place_from_headline(self, headline_text: str) -> str | None:
        """从头条文本中提取市县级地名。"""
        if not headline_text:
            return None

        segments = [seg for seg in re.split(r"与", headline_text) if seg.strip()]
        for segment in segments:
            for place in _RE_PLACE.findall(segment):
                if self._is_noise_place(place):
                    continue
                # 跳过含省级名称的宽泛匹配，优先更细的区县名
                if resolve_province_from_text(place):
                    continue
                return place

        org_match = _RE_ORG_TAIL.search(headline_text)
        if org_match:
            org_name = org_match.group(1)
            if not self._is_noise_place(org_name):
                return org_name

        fallback_text = re.split(r"气象(?:站|台)", headline_text, maxsplit=1)[0].strip()
        if fallback_text:
            fallback_text = re.sub(r"^[^\u4e00-\u9fa5]+", "", fallback_text)
            fallback_text = re.sub(r"[^\u4e00-\u9fa5]+$", "", fallback_text)
            if fallback_text and not self._is_noise_place(fallback_text):
                return fallback_text
        return None

    async def close(self) -> None:
        """关闭解析器（保持向后兼容）。"""
        pass

    async def extract_province_with_fallback(
        self,
        title_text: str,
        headline_text: str = "",
        *,
        adcode: str | None = None,
        event_id: str | None = None,
    ) -> str | None:
        """多级解析省份简称（纯本地高速匹配）。

        解析顺序：
        1. 若提供了 adcode 或符合格式的 event_id，按区划代码快速查出省份；
        2. 从标题文本提取（全称/全国级/简称）；
        3. 从头条文本提取（全称/全国级/简称）；
        4. 从头条/标题中提取市县级地名并在本地行政区划库中反查；
        5. 归纳为统一简称（如 "江苏", "广东", "全国"）。
        """
        # 第一阶段：区划代码优先
        if adcode:
            prov = self._db.query_by_adcode(adcode)
            if prov:
                return prov

        if event_id:
            prov = self._db.query_by_adcode(event_id)
            if prov:
                return prov

        # 第二阶段：从标题文本提取
        prov = resolve_province_from_text(title_text)
        if prov:
            return prov

        # 第三阶段：从头条文本提取
        prov = resolve_province_from_text(headline_text)
        if prov:
            return prov

        # 第四阶段：从头条/标题中剥离地名，在本地区划库反查
        place = self._extract_place_from_headline(headline_text)
        if not place and title_text:
            place = self._extract_place_from_headline(title_text)

        if place:
            prov = self._db.query_by_place_name(place)
            if prov:
                return prov

        return None

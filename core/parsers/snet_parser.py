"""
NIED S-Net 海底测站震度解析器。

数据来源：日本国土交通省 MSIL 强震动信息瓦片服务
  - 瓦片 URL: https://www.msil.go.jp/data/tiles/smoni/tileimage/{time}/{time}/5/28/{y}.png
  - 测站坐标: SNET_REAL_COORDS（硬编码，来源 CAPQuake）
  - 瓦片像素位置: ObsPoints.json 提取后硬编码
  - 颜色映射: HSV 多项式插值（RGB→震度）

移植自 mix 插件 parser/snet.py，并适配本仓库 BaseParser / EventEnvelope 体系。
"""

from __future__ import annotations

import base64
import colorsys
import io
from datetime import datetime, timezone
from typing import Any

from PIL import Image

from ...utils.converters import ScaleConverter
from ...utils.plugin_logger import plugin_logger
from ..domain.event_identity import EventIdentity
from ..domain.event_models import EarthquakeEvent, EventEnvelope
from ..domain.event_payload import SourcePayload
from ..services.snet.snet_filter_constants import (
    DEFAULT_MIN_SHINDO,
    DEFAULT_STATION_MIN_SHINDO,
    count_triggered_stations,
    normalize_min_shindo,
    normalize_station_min_shindo,
)
from ..sources.source_catalog import get_source_entry
from .base_parser import BaseParser

SNET_REAL_COORDS: dict[str, tuple[float, float]] = {
    "N.S1N01": (35.8968, 141.0535),
    "N.S1N02": (35.8424, 141.3772),
    "N.S1N03": (35.7203, 141.6451),
    "N.S1N04": (35.6036, 141.9041),
    "N.S1N05": (35.4047, 142.0531),
    "N.S1N06": (35.2277, 141.8692),
    "N.S1N07": (35.2773, 141.5902),
    "N.S1N08": (35.4536, 141.3898),
    "N.S1N09": (35.2270, 141.3068),
    "N.S1N10": (35.0925, 141.2021),
    "N.S1N11": (35.1203, 140.9682),
    "N.S1N12": (35.0228, 140.8091),
    "N.S1N13": (34.8788, 140.9627),
    "N.S1N14": (34.6407, 141.0907),
    "N.S1N15": (34.5256, 141.3512),
    "N.S1N16": (34.3686, 141.5402),
    "N.S1N17": (34.1956, 141.3341),
    "N.S1N18": (34.2620, 141.0316),
    "N.S1N19": (34.2269, 140.7311),
    "N.S1N20": (34.2592, 140.4159),
    "N.S1N21": (34.4231, 140.2030),
    "N.S1N22": (34.6443, 140.0906),
    "N.S2N01": (37.8428, 141.3845),
    "N.S2N02": (37.6922, 141.6387),
    "N.S2N03": (37.7073, 141.9650),
    "N.S2N04": (37.6739, 142.2975),
    "N.S2N05": (37.6016, 142.6236),
    "N.S2N06": (37.5259, 142.9350),
    "N.S2N07": (37.4290, 143.2266),
    "N.S2N08": (37.2220, 143.0700),
    "N.S2N09": (37.0741, 142.8188),
    "N.S2N10": (37.0948, 142.4979),
    "N.S2N11": (37.1931, 142.1998),
    "N.S2N12": (37.2772, 141.8790),
    "N.S2N13A": (37.3003, 141.5709),
    "N.S2N14": (37.0952, 141.3703),
    "N.S2N15": (36.8344, 141.3307),
    "N.S2N16": (36.6620, 141.5207),
    "N.S2N17": (36.6337, 141.8389),
    "N.S2N18": (36.6824, 142.1445),
    "N.S2N19": (36.5986, 142.4389),
    "N.S2N20": (36.3885, 142.6164),
    "N.S2N21": (36.1577, 142.5553),
    "N.S2N22": (35.9463, 142.4014),
    "N.S2N23": (35.9677, 142.1138),
    "N.S2N24": (35.9976, 141.7944),
    "N.S2N25": (36.0729, 141.5095),
    "N.S2N26": (36.1442, 141.2021),
    "N.S3N01": (39.4497, 142.4578),
    "N.S3N02": (39.3746, 142.7918),
    "N.S3N03": (39.3231, 143.1206),
    "N.S3N04": (39.2958, 143.4544),
    "N.S3N05": (39.1906, 143.7499),
    "N.S3N06": (39.0459, 143.9305),
    "N.S3N07": (38.8308, 143.7846),
    "N.S3N08": (38.7826, 143.4769),
    "N.S3N09": (38.7739, 143.1437),
    "N.S3N10": (38.8668, 142.8212),
    "N.S3N11": (38.9349, 142.4873),
    "N.S3N12": (38.8412, 142.1816),
    "N.S3N13": (38.5901, 142.1816),
    "N.S3N14": (38.4993, 142.5002),
    "N.S3N15": (38.4487, 142.8376),
    "N.S3N16": (38.4262, 143.1703),
    "N.S3N17": (38.3978, 143.5139),
    "N.S3N18": (38.3063, 143.7810),
    "N.S3N19": (38.0594, 143.7441),
    "N.S3N20": (37.9311, 143.5675),
    "N.S3N21": (37.9713, 143.2454),
    "N.S3N22": (37.9838, 142.9072),
    "N.S3N23": (38.0270, 142.5735),
    "N.S3N24": (38.0569, 142.2340),
    "N.S3N25": (38.0972, 141.8957),
    "N.S3N26": (38.1060, 141.5572),
    "N.S4N01": (40.7881, 141.7895),
    "N.S4N02": (40.9069, 142.1057),
    "N.S4N03": (41.0156, 142.4314),
    "N.S4N04": (41.0762, 142.7706),
    "N.S4N05": (41.0443, 143.1138),
    "N.S4N06": (40.9718, 143.4481),
    "N.S4N07": (40.8820, 143.7746),
    "N.S4N08": (40.7805, 144.0905),
    "N.S4N09": (40.5521, 144.1332),
    "N.S4N10": (40.4327, 143.8887),
    "N.S4N11": (40.4353, 143.5430),
    "N.S4N12": (40.4515, 143.2015),
    "N.S4N13": (40.5196, 142.9617),
    "N.S4N14": (40.5927, 142.6340),
    "N.S4N15": (40.5933, 142.2844),
    "N.S4N16": (40.3295, 142.2673),
    "N.S4N17": (40.1165, 142.3926),
    "N.S4N18": (40.1088, 142.6222),
    "N.S4N19": (40.0904, 142.9695),
    "N.S4N20": (40.0743, 143.3210),
    "N.S4N21": (40.0863, 143.6572),
    "N.S4N22": (40.0260, 143.9547),
    "N.S4N23": (39.7718, 143.9259),
    "N.S4N24": (39.6388, 143.7154),
    "N.S4N25": (39.6976, 143.3749),
    "N.S4N26": (39.7245, 143.0384),
    "N.S4N27": (39.7445, 142.6903),
    "N.S4N28": (39.7385, 142.3408),
    "N.S5N01": (42.7688, 145.7115),
    "N.S5N02": (42.6403, 145.4063),
    "N.S5N03": (42.4802, 145.1433),
    "N.S5N04": (42.2286, 145.2040),
    "N.S5N05": (42.0615, 145.4370),
    "N.S5N06": (41.8840, 145.6544),
    "N.S5N07": (41.6637, 145.5291),
    "N.S5N08": (41.5301, 145.2483),
    "N.S5N09": (41.5210, 144.9050),
    "N.S5N10": (41.6541, 144.6716),
    "N.S5N11": (41.9072, 144.6955),
    "N.S5N12": (42.0784, 144.6375),
    "N.S5N13": (41.9792, 144.3445),
    "N.S5N14": (41.7475, 144.1779),
    "N.S5N15": (41.4961, 144.0879),
    "N.S5N16": (41.3742, 143.8006),
    "N.S5N17": (41.3643, 143.4530),
    "N.S5N18": (41.4351, 143.1264),
    "N.S5N19": (41.5607, 142.8177),
    "N.S5N20": (41.6095, 142.4787),
    "N.S5N21": (41.4248, 142.2193),
    "N.S5N22": (41.1989, 142.0271),
    "N.S5N23": (40.9540, 141.8762),
    "N.S6N01": (42.8064, 146.0211),
    "N.S6N02": (42.5807, 146.0780),
    "N.S6N03": (42.0943, 146.2316),
    "N.S6N04": (41.6653, 146.1747),
    "N.S6N05": (41.3717, 145.6053),
    "N.S6N06": (40.8999, 145.3929),
    "N.S6N07": (40.5360, 144.9381),
    "N.S6N08": (40.0319, 144.8089),
    "N.S6N09": (39.5172, 144.7191),
    "N.S6N10": (39.0072, 144.5915),
    "N.S6N11": (38.4990, 144.4536),
    "N.S6N12": (37.9879, 144.3357),
    "N.S6N13": (37.4862, 144.1757),
    "N.S6N14": (37.0120, 143.9557),
    "N.S6N15": (36.5748, 143.6059),
    "N.S6N16": (36.1262, 143.2823),
    "N.S6N17": (35.6745, 142.9688),
    "N.S6N18": (35.2114, 142.6799),
    "N.S6N19": (34.7118, 142.5208),
    "N.S6N20": (34.2604, 142.2389),
    "N.S6N21": (33.9619, 141.7291),
    "N.S6N22": (33.8601, 141.1281),
    "N.S6N23": (33.9448, 140.5189),
    "N.S6N24": (34.1773, 139.9814),
    "N.S6N25": (34.6696, 139.8167),
    "N.ST1H": (34.5956, 139.9183),
    "N.ST2H": (34.7396, 139.8393),
    "N.ST3H": (34.7983, 139.6435),
    "N.ST4H": (34.8931, 139.5711),
    "N.ST5H": (34.9413, 139.4213),
    "N.ST6H": (35.0966, 139.3778),
}

# 瓦片内测站像素位置 (从 ObsPoints.json 提取)
_SNET_TILE_POSITIONS: dict[str, dict[str, tuple[int, int]]] = {
    "y11": {
        "N.S6N05": (241, 242),
        "N.S6N04": (253, 233),
        "N.S6N03": (254, 220),
        "N.S6N02": (251, 206),
        "N.S6N01": (250, 198),
        "N.S5N01": (243, 199),
        "N.S5N02": (237, 203),
        "N.S5N03": (230, 208),
        "N.S5N04": (231, 216),
        "N.S5N05": (237, 220),
        "N.S5N06": (242, 226),
        "N.S5N07": (239, 233),
        "N.S5N08": (232, 238),
        "N.S5N09": (224, 238),
        "N.S5N10": (220, 234),
        "N.S5N11": (220, 225),
        "N.S5N12": (219, 220),
        "N.S5N13": (212, 224),
        "N.S5N14": (209, 231),
        "N.S5N15": (207, 238),
        "N.S5N16": (200, 241),
        "N.S5N17": (192, 242),
        "N.S5N18": (184, 240),
        "N.S5N19": (178, 236),
        "N.S5N20": (170, 235),
        "N.S5N21": (164, 240),
        "N.S5N22": (160, 248),
        "N.S5N23": (156, 255),
        "N.S4N03": (169, 253),
        "N.S4N04": (176, 251),
        "N.S4N05": (184, 253),
        "N.S4N06": (192, 254),
    },
    "y12": {
        "N.S4N01": (154, 3),
        "N.S4N02": (162, 0),
        "N.S4N07": (199, 1),
        "N.S4N08": (207, 3),
        "N.S4N09": (208, 11),
        "N.S4N10": (202, 15),
        "N.S4N11": (194, 15),
        "N.S4N12": (186, 14),
        "N.S4N13": (181, 13),
        "N.S4N14": (173, 10),
        "N.S4N15": (165, 10),
        "N.S4N16": (165, 17),
        "N.S4N17": (168, 24),
        "N.S4N18": (173, 24),
        "N.S4N19": (181, 24),
        "N.S4N20": (189, 25),
        "N.S4N21": (197, 24),
        "N.S4N22": (203, 26),
        "N.S4N23": (202, 34),
        "N.S4N24": (198, 38),
        "N.S4N25": (190, 37),
        "N.S4N26": (182, 35),
        "N.S4N27": (174, 35),
        "N.S4N28": (166, 35),
        "N.S3N01": (170, 43),
        "N.S3N02": (176, 46),
        "N.S3N03": (184, 47),
        "N.S3N04": (192, 48),
        "N.S3N05": (199, 50),
        "N.S3N06": (203, 56),
        "N.S3N07": (200, 62),
        "N.S3N08": (192, 63),
        "N.S3N09": (185, 64),
        "N.S3N10": (178, 61),
        "N.S3N11": (170, 59),
        "N.S3N12": (163, 62),
        "N.S3N13": (163, 69),
        "N.S3N14": (171, 71),
        "N.S3N15": (178, 73),
        "N.S3N16": (185, 73),
        "N.S3N17": (193, 74),
        "N.S3N18": (200, 77),
        "N.S3N19": (199, 84),
        "N.S3N20": (194, 88),
        "N.S3N21": (188, 87),
        "N.S3N22": (180, 86),
        "N.S3N23": (172, 85),
        "N.S3N24": (164, 84),
        "N.S3N25": (156, 82),
        "N.S3N26": (149, 82),
        "N.S2N01": (145, 90),
        "N.S2N02": (151, 94),
        "N.S2N03": (159, 94),
        "N.S2N04": (165, 95),
        "N.S2N05": (173, 98),
        "N.S2N06": (180, 101),
        "N.S2N07": (186, 103),
        "N.S2N08": (183, 109),
        "N.S2N09": (178, 113),
        "N.S2N10": (170, 112),
        "N.S2N11": (163, 110),
        "N.S2N12": (156, 108),
        "N.S2N13": (150, 106),
        "N.S2N14": (145, 112),
        "N.S2N15": (144, 120),
        "N.S2N16": (149, 125),
        "N.S2N17": (155, 126),
        "N.S2N18": (162, 124),
        "N.S2N19": (169, 126),
        "N.S2N20": (173, 133),
        "N.S2N21": (172, 138),
        "N.S2N22": (169, 145),
        "N.S2N23": (162, 144),
        "N.S2N24": (154, 143),
        "N.S2N25": (148, 141),
        "N.S2N26": (140, 140),
        "N.S1N01": (136, 147),
        "N.S1N02": (145, 148),
        "N.S1N03": (151, 151),
        "N.S1N04": (156, 155),
        "N.S1N05": (160, 160),
        "N.S1N06": (156, 165),
        "N.S1N07": (150, 164),
        "N.S1N08": (145, 159),
        "N.S1N09": (142, 165),
        "N.S1N10": (140, 169),
        "N.S1N11": (134, 168),
        "N.S1N12": (131, 171),
        "N.S1N13": (134, 174),
        "N.S1N14": (137, 181),
        "N.S1N15": (144, 184),
        "N.S1N16": (149, 189),
        "N.S1N17": (144, 193),
        "N.S1N18": (136, 191),
        "N.S1N19": (130, 192),
        "N.S1N20": (122, 191),
        "N.S1N21": (117, 187),
        "N.S1N22": (115, 181),
        "N.ST1H": (111, 183),
        "N.ST2H": (110, 178),
        "N.ST3H": (105, 177),
        "N.ST4H": (103, 175),
        "N.ST5H": (100, 173),
        "N.ST6H": (99, 171),
        "N.S6N25": (109, 181),
        "N.S6N24": (112, 193),
        "N.S6N23": (124, 200),
        "N.S6N22": (139, 203),
        "N.S6N21": (153, 200),
        "N.S6N20": (164, 191),
        "N.S6N19": (171, 179),
        "N.S6N18": (174, 166),
        "N.S6N17": (181, 152),
        "N.S6N16": (188, 140),
        "N.S6N15": (195, 127),
        "N.S6N14": (203, 114),
        "N.S6N13": (209, 101),
        "N.S6N12": (212, 86),
        "N.S6N11": (214, 71),
        "N.S6N10": (218, 57),
        "N.S6N09": (221, 41),
        "N.S6N08": (222, 26),
        "N.S6N07": (225, 11),
        "N.S6N06": (235, 0),
    },
}

_STATION_ALIAS: dict[str, str] = {
    "N.S2N13": "N.S2N13A",
}


def _rgb_to_shindo(r: int, g: int, b: int) -> float | None:
    """HSV 多项式插值：RGB → 震度值。"""
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    if v <= 0.1 or s <= 0.75:
        return None
    if h > 0.1476:
        p = (
            280.31 * h**6
            - 916.05 * h**5
            + 1142.6 * h**4
            - 709.95 * h**3
            + 234.65 * h**2
            - 40.27 * h
            + 3.2217
        )
    elif h > 0.001:
        p = 151.4 * h**4 - 49.32 * h**3 + 6.753 * h**2 - 2.481 * h + 0.9033
    else:
        p = -0.005171 * v**2 - 0.3282 * v + 1.2236
    if p < 0:
        p = 0.0
    return p * 10.0 - 3.0


def _decode_pixel(png_bytes: bytes, x: int, y: int) -> tuple[int, int, int] | None:
    """解码 PNG，返回 (x,y) 处 RGB。"""
    try:
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        return img.getpixel((x, y))
    except Exception:
        return None


def _build_stations(tiles: dict[str, Image.Image]) -> list[dict[str, Any]]:
    """遍历所有测站，解码 RGB → 震度。"""
    stations = []
    for tile_name, station_dict in _SNET_TILE_POSITIONS.items():
        tile_img = tiles.get(tile_name)
        if tile_img is None:
            continue
        for obs_name, (px, py) in station_dict.items():
            real_name = _STATION_ALIAS.get(obs_name, obs_name)
            coords = SNET_REAL_COORDS.get(real_name)
            if coords is None:
                continue
            lat, lon = coords
            try:
                rgb = tile_img.getpixel((px, py))
                if len(rgb) >= 3:
                    r, g, b = rgb[:3]
                else:
                    continue
            except Exception:
                continue
            shindo = _rgb_to_shindo(r, g, b)
            if shindo is None:
                continue
            stations.append(
                {
                    "name": real_name,
                    "lat": lat,
                    "lon": lon,
                    "shindo": shindo,
                    "rgb": (r, g, b),
                    "tile": tile_name,
                    "px": px,
                    "py": py,
                }
            )
    return stations


class SnetParser(BaseParser):
    """NIED S-Net 海底测站震度解析器。"""

    def __init__(self, message_logger=None):
        super().__init__("snet_msil", message_logger)

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        """解析 S-Net 瓦片载荷。

        期望格式:
        {
            "tiles": {"y11": "<base64>", "y12": "<base64>"},
            "timestamp": "20240101120000",
            "min_shindo": 0.5,
            "stations": [...],          # 可选，预解析测站
            "triggered": [...],         # 可选
        }
        """
        if not isinstance(data, dict):
            return None

        timestamp = str(data.get("timestamp") or "").strip()
        min_shindo = normalize_min_shindo(data.get("min_shindo", DEFAULT_MIN_SHINDO))
        station_min_shindo = normalize_station_min_shindo(
            data.get("station_min_shindo", DEFAULT_STATION_MIN_SHINDO)
        )

        # 取两者中较小者作为解析阶段的触发测站过滤门槛
        fetch_min_shindo = min(min_shindo, station_min_shindo)

        all_stations = data.get("stations")
        if not isinstance(all_stations, list) or not all_stations:
            raw_tiles = data.get("tiles", {})
            if not isinstance(raw_tiles, dict) or not raw_tiles:
                return None
            decoded: dict[str, Image.Image] = {}
            for tn in ("y11", "y12"):
                b64 = raw_tiles.get(tn)
                if not b64:
                    continue
                try:
                    png = base64.b64decode(b64)
                    decoded[tn] = Image.open(io.BytesIO(png)).convert("RGB")
                except Exception as exc:
                    plugin_logger.warning(
                        f"[灾害预警] {self.source_id} 瓦片解码失败 {tn}: {exc}"
                    )
            if not decoded:
                return None
            all_stations = _build_stations(decoded)

        if not all_stations:
            return None

        # 仅保留可序列化字段，避免 Image/tuple 等污染 metadata
        normalized_stations: list[dict[str, Any]] = []
        for item in all_stations:
            if not isinstance(item, dict):
                continue
            rgb = item.get("rgb")
            if isinstance(rgb, tuple):
                rgb = list(rgb)
            normalized_stations.append(
                {
                    "name": str(item.get("name") or ""),
                    "lat": float(item.get("lat") or 0.0),
                    "lon": float(item.get("lon") or 0.0),
                    "shindo": float(item.get("shindo") or 0.0),
                    "rgb": rgb if isinstance(rgb, list) else None,
                    "tile": str(item.get("tile") or ""),
                    "px": int(item.get("px") or 0),
                    "py": int(item.get("py") or 0),
                }
            )
        if not normalized_stations:
            return None

        sorted_stations = sorted(
            normalized_stations,
            key=lambda s: s.get("shindo", -999.0),
            reverse=True,
        )
        triggered = [
            s
            for s in sorted_stations
            if float(s.get("shindo", -999.0)) >= fetch_min_shindo
        ]
        if not triggered:
            plugin_logger.debug(
                f"[灾害预警] {self.source_id} 无测站达到阈值 ({fetch_min_shindo})"
            )
            return None

        top = triggered[0]
        max_shindo = float(top.get("shindo") or 0.0)

        occurred_at = None
        if timestamp:
            try:
                occurred_at = datetime.strptime(timestamp, "%Y%m%d%H%M00").replace(
                    tzinfo=timezone.utc
                )
            except (ValueError, TypeError):
                occurred_at = None
        if occurred_at is None:
            occurred_at = datetime.now(timezone.utc)

        source_entry = get_source_entry(self.source_id)
        event_id = (
            f"snet_{timestamp}" if timestamp else f"snet_{int(occurred_at.timestamp())}"
        )

        # 重新为 metadata 包含正确的 triggered_count，以便 intensity_rule.py 过滤
        # 基于 min_shindo / station_min_shindo 分别统计
        triggered_count = count_triggered_stations(sorted_stations, min_shindo)
        triggered_station_count = count_triggered_stations(
            sorted_stations, station_min_shindo
        )

        metadata = {
            "stations": normalized_stations,
            "triggered": triggered,
            "timestamp": timestamp,
            "min_shindo": min_shindo,
            "station_min_shindo": station_min_shindo,
            "max_shindo": max_shindo,
            "triggered_count": triggered_count,
            "triggered_station_count": triggered_station_count,
            "total_stations": len(normalized_stations),
            "top_station": top.get("name"),
            "source_family": "direct_http",
            "source_enum": source_entry.source_enum if source_entry else "snet_msil",
            "source_type": source_entry.source_type.value
            if source_entry
            else "earthquake_info",
        }

        scale_label = ScaleConverter.format_measured_intensity_display(max_shindo)
        top_station_name = str(top.get("name") or "未知测站")
        domain_event = EarthquakeEvent(
            occurred_at=occurred_at,
            latitude=float(top.get("lat") or 0.0),
            longitude=float(top.get("lon") or 0.0),
            place_name="日本海沟 S-Net 海底观测网",
            magnitude=None,
            depth=None,
            intensity=None,
            scale=max_shindo,
            headline=f"S-Net 最大震度 {scale_label or '不明'}",
            metadata=dict(metadata),
        )

        identity = EventIdentity(
            event_id=event_id,
            source_id=self.source_id,
            event_type="earthquake",
            provider_family=source_entry.provider_family.value
            if source_entry
            else "direct_http",
            source_enum=source_entry.source_enum if source_entry else "snet_msil",
            published_at=occurred_at,
            aliases=(event_id,),
            attributes={"timestamp": timestamp, "max_shindo": max_shindo},
        )

        envelope = EventEnvelope(
            identity=identity,
            event=domain_event,
            payload=SourcePayload(
                source_id=self.source_id,
                provider_family=identity.provider_family,
                message_type="snet_tiles",
                raw={
                    "timestamp": timestamp,
                    "min_shindo": min_shindo,
                    "station_min_shindo": station_min_shindo,
                    "triggered_count": triggered_count,
                    "triggered_station_count": triggered_station_count,
                    "total_stations": len(normalized_stations),
                    # tiles 体积大，仅在上游未预解析时保留
                    "tiles": data.get("tiles") if not data.get("stations") else {},
                },
            ),
            metadata=metadata,
        )

        # 默认 DEBUG；达到震度 1（計測震度 >= 0.5）时升为 INFO。
        # 推送路径仅在有 triggered 时进入，不再用 min_shindo 判定（否则几乎恒为 INFO）。
        log_message = (
            f"[灾害预警] NIED S-Net 海底震度解析成功: {top_station_name}, "
            f"震度: {scale_label or max_shindo}, 时间: {occurred_at}"
        )
        if max_shindo >= 0.5:
            plugin_logger.info(
                log_message,
                is_event_linked=True,
                event_stream="earthquake",
                is_silent_window=True,
            )
        else:
            plugin_logger.debug(log_message)

        return envelope


# 便于服务层复用
MSIL_TILE_BASE = "https://www.msil.go.jp/data/tiles/smoni/tileimage"

__all__ = [
    "SnetParser",
    "SNET_REAL_COORDS",
    "_build_stations",
    "_rgb_to_shindo",
    "MSIL_TILE_BASE",
]

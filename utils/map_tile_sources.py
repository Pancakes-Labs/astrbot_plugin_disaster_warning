"""
地图瓦片源URL配置
统一管理所有地图瓦片的URL模板
"""

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

# 地图瓦片源URL映射
MAP_TILE_SOURCES = {
    # 高德地图（直接访问官方服务器，需要 {s} 作为子域名占位符）
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


def get_tile_url_js(map_source: str) -> str:
    """
    为JavaScript生成瓦片URL（处理特殊占位符）

    Args:
        map_source: 地图源标识符（中文名称或英文标识）

    Returns:
        适用于JavaScript的URL字符串
    """
    source_id = normalize_map_source(map_source)
    url = get_tile_url(map_source)

    # 高德地图需要子域名轮询（{s} -> 随机1-4）
    if source_id == "amap":
        # JavaScript中使用模板字符串处理
        return url.replace("{s}", '${["1","2","3","4"][Math.floor(Math.random()*4)]}')

    return url

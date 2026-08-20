# FAN Studio TileMap API

## 如何使用

请在您的地图应用中使用以下格式的 URL ：

`https://tilemap.fanstudio.tech/{source}/{z}/{x}/{y} (或 {z}/{y}/{x})`

其中 `{source}` 是下方列出的地图源标识之一。无需添加.png 等后缀。

## 可用的公开地图源

### ArcGIS 卫星影像

地图源标识 (source): `arcwi`

**URL:**

`https://tilemap.fanstudio.tech/arcwi/{z}/{y}/{x}`

[查看示例瓦片](https://tilemap.fanstudio.tech/arcwi/7/52/101)

### ArcGIS 地形图

地图源标识 (source): `arcwob`

**URL:**

`https://tilemap.fanstudio.tech/arcwob/{z}/{y}/{x}`

[查看示例瓦片](https://tilemap.fanstudio.tech/arcwob/7/52/101)

### ArcGIS 山影图

地图源标识 (source): `arcwh`

**URL:**

`https://tilemap.fanstudio.tech/arcwh/{z}/{y}/{x}`

[查看示例瓦片](https://tilemap.fanstudio.tech/arcwh/7/52/101)

### DEM高程数据

地图源标识 (source): `dem`

**URL:**

`https://tilemap.fanstudio.tech/dem/{z}/{x}/{y}`

[查看示例瓦片](https://tilemap.fanstudio.tech/dem/7/52/101)

### PetalMap 矢量图 亮

地图源标识 (source): `petallight`

**URL:**

`https://tilemap.fanstudio.tech/petallight/{z}/{x}/{y}`

[查看示例瓦片](https://tilemap.fanstudio.tech/petallight/7/52/101)

### PetalMap 矢量图 暗

地图源标识 (source): `petaldark`

**URL:**

`https://tilemap.fanstudio.tech/petaldark/{z}/{x}/{y}`

[查看示例瓦片](https://tilemap.fanstudio.tech/petaldark/7/52/101)

### 电子海图

地图源标识 (source): `enc`

**URL:**

`https://tilemap.fanstudio.tech/enc/{z}/{y}/{x}`

[查看示例瓦片](https://tilemap.fanstudio.tech/enc/7/52/101)

### 实况云图层

地图源标识 (source): `cloud`

**URL:**

`https://tilemap.fanstudio.tech/cloud/{z}/{x}/{y}`

[查看示例瓦片](https://tilemap.fanstudio.tech/cloud/7/52/101)

### 实况降水图层

地图源标识 (source): `rain`

**URL:**

`https://tilemap.fanstudio.tech/rain/{z}/{x}/{y}`

[查看示例瓦片](https://tilemap.fanstudio.tech/rain/7/52/101)

### 实况风图层

地图源标识 (source): `wind`

**URL:**

`https://tilemap.fanstudio.tech/wind/{z}/{x}/{y}`

[查看示例瓦片](https://tilemap.fanstudio.tech/wind/7/52/101)

### 中国境内等高线

地图源标识 (source): `cncl`

**URL:**

`https://tilemap.fanstudio.tech/cncl/{z}/{x}/{y}`

[查看示例瓦片](https://tilemap.fanstudio.tech/cncl/7/52/101)

---

以下是曾经支持但现已移除出文档的瓦片源：

### 中科星图 卫星影像

地图源标识 (source): `geovis`

**URL:**

`https://tilemap.fanstudio.tech/geovis/{z}/{y}/{x}`

[查看示例瓦片](https://tilemap.fanstudio.tech/geovis/7/52/101)

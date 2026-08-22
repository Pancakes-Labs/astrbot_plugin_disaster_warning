"""
消息 JSON 展示格式化服务。
负责键名翻译、值格式化与递归 JSON 文本渲染，
用于收缩 core/message/message_logger.py 中的展示规则实现。
"""

from __future__ import annotations

from typing import Any


class MessageJsonFormatterService:
    """消息 JSON 展示格式化服务。"""

    _KEY_MAPPINGS = {
        # 🌍 基础信息字段 (所有数据源通用)
        "id": "ID",
        "ID": "ID",
        "_id": "数据库ID",
        "type": "消息类型",
        "title": "标题",
        "key": "编号",
        "code": "消息代码",
        "source": "数据来源",
        "status": "状态",
        "action": "操作",
        "timestamp": "时间戳",
        "time": "发生时间",
        "createTime": "创建时间",
        "updateTime": "更新时间",
        "created_at": "创建时间",
        "updated_at": "更新时间",
        "started_at": "开始时间",
        "expire": "过期时间",
        # 🌀 台风相关字段（Fan Studio / EQSC）
        "typhoon": "台风列表",
        "nameEN": "英文名",
        "nameCN": "中文名",
        "name_en": "英文名",
        "isActive": "是否活跃",
        "historyTrack": "历史路径",
        "futureTrack": "预测路径",
        "history_track": "历史路径",
        "future_track": "预测路径",
        "typeNameCN": "强度等级(中文)",
        "pressure": "中心气压(hPa)",
        "speed": "移动速度(km/h)",
        "direction": "移动方向",
        "directionCN": "移动方向(中文)",
        "moveDirection": "移动方向",
        "moveSpeed": "移动速度(km/h)",
        "windSpeed": "最大风速(m/s)",
        "power": "中心风力(级)",
        "radius7": "七级风圈半径(km)",
        "radius10": "十级风圈半径(km)",
        "windCircle": "风圈半径",
        "wind_circle": "风圈半径",
        "30KTS": "七级风圈(30KTS)",
        "50KTS": "十级风圈(50KTS)",
        "64KTS": "十二级风圈(64KTS)",
        "NE": "东北象限(km)",
        "SE": "东南象限(km)",
        "SW": "西南象限(km)",
        "NW": "西北象限(km)",
        "Data": "数据主体",
        "provider": "数据服务商",
        # 🏔️ 地震核心信息
        "earthquake": "地震信息",
        "magnitude": "震级",
        "Magunitude": "震级",  # Wolfx拼写
        "depth": "深度(km)",
        "Depth": "深度(km)",  # 大写版本
        "latitude": "纬度",
        "Latitude": "纬度",  # 大写版本
        "longitude": "经度",
        "Longitude": "经度",  # 大写版本
        "placeName": "地名",
        "name": "名称",  # 通用：地名/台风中文名等
        "shockTime": "发震时间",
        "OriginTime": "发震时间",  # JMA格式
        "place": "震中",
        "region": "震中",  # Global Quake格式
        "hypocenter": "震源信息",
        "Hypocenter": "震源地名",  # JMA格式
        # 📍 震度/烈度信息
        "maxScale": "最大震度(原始)",
        "MaxIntensity": "最大烈度/震度",  # JMA/Wolfx格式
        "maxIntensity": "最大烈度",  # Wolfx格式
        "epiIntensity": "预估烈度",  # FAN Studio格式
        "intensity": "烈度",
        "shindo": "震度",  # JMA格式
        "scale": "震度值",  # P2P格式
        # 🌊 海啸相关信息
        "domesticTsunami": "日本境内海啸",
        "foreignTsunami": "海外海啸",
        "tsunami": "海啸信息",
        "info": "海啸信息",  # Wolfx格式
        # 📋 事件标识信息
        "eventId": "事件ID",
        "EventID": "事件ID",  # JMA格式
        "event_id": "事件ID",  # 下划线版本
        "EventId": "事件编码",  # FAN Studio格式
        "uniEventId": "事件唯一ID",  # CENC 烈度速报
        "locName": "震中地名",
        "nameByInfo": "报告标题",
        "oriTime": "发震时间",
        "gmtCreate": "报告生成时间",
        "focDepth": "震源深度(km)",
        "intensity_info_text": "烈度概述",
        "instrument_intensity_json": "台站仪器烈度",
        "contour_geojson": "等震线GeoJSON",
        "subjectCodes": "报告主题编码",
        "Serial": "报序号",  # JMA格式
        "updates": "更新次数",
        "ReportNum": "发报数",  # Wolfx格式
        # ⏰ 时间相关
        "AnnouncedTime": "发布时间",  # JMA格式
        "ReportTime": "发报时间",  # Wolfx格式
        "time_full": "发报时间(完整)",
        "originTimeMs": "发震时间(MS)",
        "originTimeIso": "发震时间(ISO)",
        "lastUpdateMs": "最后更新(MS)",
        "effective": "生效时间",  # FAN Studio格式
        "issue_time": "发布时间",
        "arrivalTime": "到达时间",  # 海啸
        # 🎯 状态标志
        "isFinal": "最终报",
        "final": "最终报",  # FAN Studio格式
        "isCancel": "取消报",
        "cancel": "取消报",  # FAN Studio格式
        "is_final": "最终报",
        "is_cancel": "取消报",
        "cancelled": "取消标志",  # P2P格式
        "fixedDepth": "固定深度",
        "is_training": "训练模式",
        "isTraining": "训练报",  # Wolfx格式
        "isSea": "海域地震",  # Wolfx格式
        "isAssumption": "推定震源",  # Wolfx格式
        "isWarn": "警报标志",  # Wolfx格式
        "immediate": "紧急标志",  # 海啸
        # 📰 内容描述
        "headline": "预警标题",  # FAN Studio格式
        "description": "详细描述",  # FAN Studio格式
        "infoTypeName": "信息类型",  # FAN Studio格式
        "correct": "订正信息",
        "issue": "发布信息",
        # 🗺️ 地理区域
        "province": "省份",  # FAN Studio格式
        "pref": "都道府县",  # P2P格式
        "addr": "观测点地址",  # P2P格式
        "location": "震源地",  # Wolfx格式
        "area": "区域代码",  # P2P格式
        "isArea": "区域标志",  # P2P格式
        # 🔗 链接和参考
        "url": "官方链接",
        "OriginalText": "原电文",  # Wolfx格式
        # 📊 精度和可信度
        "Accuracy.Epicenter": "震中精度",  # Wolfx格式
        "Accuracy.Depth": "深度精度",  # Wolfx格式
        "Accuracy.Magnitude": "震级精度",  # Wolfx格式
        "confidence": "可信度",  # P2P格式
        # 🌊 海啸详细信息
        "warningInfo": "警报核心信息",
        "timeInfo": "时间信息",
        "details": "详细信息",
        "forecasts": "沿海预报",
        "waterLevelMonitoring": "水位监测",
        "estimatedArrivalTime": "预计到达时间",
        "maxWaveHeight": "最大波高",
        "warningLevel": "警报级别",
        "stationName": "监测站名称",
        "firstHeight": "初波信息",  # 海啸
        "maxHeight": "最大波高",  # 海啸
        "condition": "状态描述",  # 海啸
        "grade": "预警级别",  # 海啸
        # 📍 观测点信息 (P2P)
        "points": "震度观测点",
        "comments": "附加评论",
        "freeFormComment": "自由附加文",
        "areas": "预警区域",  # 海啸和P2P
        # ⚠️ 变更和警报信息
        "MaxIntChange.String": "震度变更说明",  # Wolfx格式
        "MaxIntChange.Reason": "震度变更原因",  # Wolfx格式
        "CodeType": "发报说明",  # Wolfx格式
        "Title": "发报报头",  # Wolfx格式
        # 🔧 技术字段
        "hop": "跳数(hop)",
        "uid": "用户ID",
        "ver": "版本号",
        "user-agent": "客户端标识",
        "count": "计数",
        "area_confidences": "区域置信度",
        "autoFlag": "自动标志",  # FAN Studio格式
        "earthtype": "地震类型",  # FAN Studio格式
        "md5": "校验码",
        "revisionId": "修订版本号",
        "maxPGA": "最大地表加速度",
        "cluster": "集群信息",
        "level": "级别",
        "quality": "质量指标",
        "errOrigin": "时间误差",
        "errDepth": "深度误差",
        "errNS": "南北向误差",
        "errEW": "东西向误差",
        "pct": "置信度百分比",
        "stations": "参与定位的台站数",
        "stationCount": "台站统计",
        "total": "总可用台站数",
        "selected": "被选中参与计算的台站数",
        "used": "实际用于定位的台站数",
        "matching": "匹配度高的台站数",
        "depthConfidence": "深度置信度",
        "minDepth": "最小深度",
        "maxDepth": "最大深度",
        # 🔌 连接信息 (保留原有)
        "connection_type": "连接类型",
        "server": "服务器",
        "port": "端口",
        "status_code": "状态码",
        # 🌐 协议/封装层字段 (Global Quake / OpenQuakeAPI / Fan Studio)
        "timestampMs": "时间戳(毫秒)",
        "payload": "数据载荷",
        "protobuf": "Protobuf格式",
        "data": "数据",
        "message": "消息内容",
        "summary": "摘要标志",
        "total_events": "事件总数",
        "note": "备注说明",
        "preview": "预览",
        "_truncated": "内容已截断",
        "_original_chars": "原始字符数",
        # 🗂️ EQSC 列表/事件字段
        "eventID": "事件ID",  # EQSC格式
        "reportTime": "发报时间",  # EQSC/JMA格式（camelCase）
        "register": "登记时间",  # EQSC/P2P格式
        "expiresAt": "过期时间",  # EQSC格式
        "originTime": "发震时间",  # 小写camelCase
        "issueHypocenter": "震源信息",  # EQSC格式
        "hypoCenterName": "震源地名称",  # EQSC格式
        # Wolfx / JMA 大写变体
        "Magnitude": "震级",  # Wolfx/JMA大写
        "HypoCenter": "震源中心",  # Wolfx格式
        "Issue": "发布信息",  # Wolfx格式
        "Source": "情报来源",  # Wolfx格式
        "Status": "情报状态",  # Wolfx格式
        "Accuracy": "精度信息",  # Wolfx格式
        "Epicenter": "震中",  # Wolfx精度子字段
        "MaxIntChange": "最大震度变化",  # Wolfx格式
        "String": "变化说明",  # Wolfx格式
        "Reason": "变化原因",  # Wolfx格式
        "WarnArea": "预警区域",  # Wolfx格式
        "Pond": "P2P区域代码",  # Wolfx格式
        "Chiiki": "地域名称",  # Wolfx预警区域子字段
        "Shindo1": "预测震度(上)",  # Wolfx格式
        "Shindo2": "预测震度(下)",  # Wolfx格式
        "Time": "时刻",  # Wolfx预警区域子字段
        "Type": "类型",  # Wolfx预警区域子字段
        "Arrive": "到达预测",  # Wolfx格式
        # JMA 情报字段 (Global Quake payload)
        "codeType": "情报类型",  # JMA格式
        "announcedTime": "发布时间",  # JMA格式（camelCase）
        "targetTime": "目标时间",  # JMA格式
        "publishingOffice": "发布机构",  # JMA格式
        "editorialOffice": "编辑机构",  # JMA格式
        "originalText": "原电文",  # JMA格式（camelCase）
        "serial": "报序号",  # JMA/P2P格式（camelCase）
        # P2P 数据源字段
        "convert": "转换时间",  # P2P格式
        "user_agent": "客户端标识",  # P2P格式（下划线版）
        "kindCode": "种类代码",  # P2P格式
        "scaleFrom": "震度下限",  # P2P格式
        "scaleTo": "震度上限",  # P2P格式
        "reduceName": "简略地域名",  # P2P格式
        "display": "显示标识",  # P2P格式
        # Fan Studio 子源分组键
        "fssn": "FSSN地震速报",
        "fssn-cmt": "FSSN矩心矩张量解",
        "weatheralarm": "气象预警",
        "cenc": "中国地震台网",
        "cea": "中国地震预警网",
        "cea-pr": "中国地震预警网（省级）",
        "ningxia": "宁夏地震台网",
        "guangxi": "广西地震台网",
        "shanxi": "山西地震台网",
        "beijing": "北京地震台网",
        "yunnan": "云南地震台网",
        "cwa": "台湾中央气象署地震报告",
        "cwa-eew": "台湾中央气象署强震即时警报",
        "jma": "日本气象厅",
        "hko": "香港天文台",
        "usgs": "美国地质调查局",
        "emsc": "欧洲地中海地震中心",
        "bcsf": "BCSF地震台网",
        "gfz": "德国地球科学中心",
        "usp": "USP地震台网",
        "kma": "韩国气象厅",
        "kma-eew": "韩国地震速报",
        "sa": "美国 ShakeAlert",
        "gq": "Global Quake",
        # Fan Studio 常规字段补充
        "magnitudel": "震级",  # FAN Studio云南格式
        "placeName_zh": "中文地名",  # FAN Studio格式
        "citystring": "城市描述",  # HKO/FAN Studio格式
        "verify": "核实标志",  # HKO/FAN Studio格式
        "imageURI": "图片地址",  # CWA格式
        "shakemapURI": "等震度图地址",  # CWA格式
        # 📐 CENC 烈度速报 (Fan Studio cenc-ir)
        "epiLon": "震中经度",
        "epiLat": "震中纬度",
        "raw_event_json": "原始事件数据",
        "origin": "震源信息",
        "eqType": "地震类型",
        "magNum": "震级数值",
        "magType": "震级类型",
        "costTime": "计算耗时",
        "trigStaNum": "触发台站数",
        "sendTime": "发送时间",
        "geometry": "几何信息",  # GeoJSON
        "properties": "属性信息",  # GeoJSON
        "INT": "烈度",  # 等震线GeoJSON属性
        "F_AREA": "影响面积",  # 等震线GeoJSON属性
        "Mag": "震级",  # 台站观测
        "PGA": "峰值加速度",  # 台站观测
        "PGV": "峰值速度",  # 台站观测
        "tag": "标签",
        "City": "城市",
        "County": "县区",
        "Province": "省份",
        "Site": "场地",
        "Town": "城镇",
        "Dist": "距离",
        "Date": "日期",
        "IPGA": "仪器烈度(PGA)",
        "IPGV": "仪器烈度(PGV)",
        "Vs30": "剪切波速Vs30",
        "evdp": "事件深度",
        "evla": "事件纬度",
        "evlo": "事件经度",
        "evName": "事件名称",
        "stID": "台站ID",
        "stName": "台站名称",
        "stla": "台站纬度",
        "stlo": "台站经度",
        "IDiff": "到时差",
        "PGA_E": "PGA东西向",
        "PGA_N": "PGA南北向",
        "PGA_Z": "PGA垂直向",
        "PGV_E": "PGV东西向",
        "PGV_N": "PGV南北向",
        "PGV_Z": "PGV垂直向",
        "network": "台网",
        "Unnamed": "未命名列",
        "estimateInt": "预估烈度",
        # 🔬 FSSN CMT 矩张量字段
        "allMagnitudes": "全震级列表",
        "M": "主震级",
        "mB": "体波震级(mB)",
        "mb": "体波震级(mb)",
        "MLv": "面波震级(MLv)",
        "Mwp": "W相位震级(Mwp)",
        "Mww": "矩震级(Mww)",
        "Mj": "日本气象厅震级(Mj)",
        "Mw(mB)": "矩震级Mw(mB)",
        "Mw(Mwp)": "矩震级Mw(Mwp)",
        "centroidDepth": "质心深度",
        "nodalPlane1": "节面1",
        "nodalPlane2": "节面2",
        "mnn": "矩张量分量Mnn",
        "mee": "矩张量分量Mee",
        "mdd": "矩张量分量Mdd",
        "mne": "矩张量分量Mne",
        "mnd": "矩张量分量Mnd",
        "med": "矩张量分量Med",
        # 🌊 NMEFC 海啸预警字段
        "orgUnit": "发布单位",
        "issueTime": "发布时间",  # NMEFC格式
        "depthKm": "深度(km)",  # NMEFC格式
        "depthDescription": "深度描述",  # NMEFC格式
        "earthquakeDescription": "地震描述",  # NMEFC格式
        "assessment": "评估结论",  # NMEFC格式
        "followUpNote": "后续跟踪说明",  # NMEFC格式
        "waterLevelNote": "水位说明",  # NMEFC格式
        "classificationNote": "分类说明",  # NMEFC格式
        "dutyOfficer": "值班员",  # NMEFC格式
        "dutyPhone": "值班电话",  # NMEFC格式
        "supervisingAuthority": "监管机构",  # NMEFC格式
        "earthquakePositionImageUrl": "震中位置图",  # NMEFC格式
        "signatureImageUrl": "签名图",  # NMEFC格式
        "waterLevels": "水位监测列表",  # NMEFC格式
        "coordinates": "坐标",  # NMEFC格式
        "timeBjt": "北京时间",  # NMEFC格式
        "maxAmplitudeCm": "最大波幅(cm)",  # NMEFC格式
        # 🌊 NMEFC 海浪警报字段
        "warnType": "警报类型",
        "author": "作者",
        "logo": "标识图",
        "image": "图片",
        "signUrl": "签名图地址",
        "phone": "联系电话",
        # 🌊 海啸详情字段
        "subtitle": "副标题",
        "alarmDate": "警报日期",
        "updateDate": "更新日期",
        "shockInfo": "地震信息",
        "batch": "批次",
        "logoUrl": "标识图地址",
        "htmlUrl": "详情链接",
        "maps": "图件集合",
        "earthquakeMapUrl": "震中图地址",
        "amplitudeMapUrl": "波幅图地址",
        "coastalMapUrl": "沿岸图地址",
    }

    _MAX_SCALE_MAP = {
        10: "震度1",
        20: "震度2",
        30: "震度3",
        40: "震度4",
        45: "震度5弱",
        50: "震度5強",
        55: "震度6弱",
        60: "震度6強",
        70: "震度7",
    }

    _LEVEL_MAP = {
        0: "0: 弱 (4+台站近距离触发)",
        1: "1: 中 (7+台站>64计数 或 4+台站>1,000计数)",
        2: "2: 强 (7+台站>1,000计数 或 3+台站>10,000计数)",
        3: "3: 极强 (5+台站>10,000计数 或 3+台站>50,000计数)",
        4: "4: 毁灭 (4+台站>50,000计数)",
    }

    def __init__(self, logger_instance):
        # 通过注入记录器实例复用区域映射等只读上下文，避免重复持有同类数据。
        self.logger = logger_instance

    def format_json_data(self, data: dict[str, Any], indent: int = 0) -> str:
        """递归格式化 JSON 数据，增加可读性。"""
        # 这里输出的是“人类可读日志文本”，因此优先追求字段可解释性而非 JSON 原样保真。
        result = ""
        indent_str = "  " * indent

        for key, value in data.items():
            key_display = self.get_display_key(key)

            if isinstance(value, dict):
                result += f"{indent_str}📋 {key_display}:\n"
                result += self.format_json_data(value, indent + 1)
            elif isinstance(value, list):
                if value:
                    result += f"{indent_str}📋 {key_display} ({len(value)}项):\n"
                    for i, item in enumerate(value[:5]):
                        if isinstance(item, dict):
                            result += f"{indent_str}  [{i + 1}]:\n"
                            result += self.format_json_data(item, indent + 2)
                        else:
                            result += f"{indent_str}  [{i + 1}]: {item}\n"
                    if len(value) > 5:
                        result += f"{indent_str}  ... 还有 {len(value) - 5} 项\n"
                else:
                    result += f"{indent_str}📋 {key_display}: []\n"
            else:
                result += (
                    f"{indent_str}📋 {key_display}: {self.format_value(key, value)}\n"
                )

        return result

    def get_display_key(self, key: str) -> str:
        """获取格式化后的键名显示。"""
        return self._KEY_MAPPINGS.get(key, key)

    def format_value(self, key: str, value: Any) -> str:
        """格式化具体值。"""
        if value is None:
            return "无数据"
        if value == "":
            return "空字符串"
        # EQSC 台风路径字段常用 "NULL" 表示缺失
        if isinstance(value, str) and value.strip().upper() in {"NULL", "NONE"}:
            return "无数据"
        if isinstance(value, bool):
            return "是" if value else "否"
        if isinstance(value, (int, float)):
            return self._format_numeric_value(key, value)
        if isinstance(value, str):
            return f"{value[:47]}..." if len(value) > 50 else value
        return str(value)

    def _format_numeric_value(self, key: str, value: int | float) -> str:
        """按字段语义格式化数值类型。"""
        if key == "maxScale" and isinstance(value, int):
            return f"{value} ({self._MAX_SCALE_MAP.get(value, '未知')})"
        if key in ["magnitude", "Magnitude", "Magunitude"]:
            return f"M{value:.2f}" if isinstance(value, float) else f"M{value}"
        if key in ["depth", "Depth"]:
            return f"{value:.2f}km" if isinstance(value, float) else f"{value}km"
        if key in ["latitude", "Latitude", "longitude", "Longitude"]:
            return f"{value:.5f}"
        # 台风气压 / 风速 / 移速 / 风圈半径
        if key in ["pressure", "Pressure"]:
            return f"{value} hPa"
        if key in ["windSpeed", "WindSpeed"]:
            if isinstance(value, float) and not value.is_integer():
                return f"{value:.1f} m/s"
            return f"{int(value) if float(value).is_integer() else value} m/s"
        if key in ["moveSpeed", "speed", "Speed"]:
            # EQSC speed 可能是数值，也可能是 STNR 字符串（字符串分支已处理）
            if isinstance(value, float) and not value.is_integer():
                return f"{value:.1f} km/h"
            return f"{int(value) if float(value).is_integer() else value} km/h"
        if key in ["radius7", "radius10", "NE", "SE", "SW", "NW"]:
            if isinstance(value, float) and not value.is_integer():
                return f"{value:.1f} km"
            return f"{int(value) if float(value).is_integer() else value} km"
        if key == "power":
            return f"{int(value) if float(value).is_integer() else value} 级"
        if key in [
            "maxPGA",
            "errOrigin",
            "errDepth",
            "errNS",
            "errEW",
            "pct",
            "minDepth",
            "maxDepth",
        ] and isinstance(value, float):
            return f"{value:.3f}"
        if key == "area" and isinstance(value, int):
            region_name = self.logger.p2p_area_mapping.get(value, f"区域代码{value}")
            return f"{value} ({region_name})"
        if key == "level" and isinstance(value, int):
            return f"{value} ({self._LEVEL_MAP.get(value, '未知级别')})"
        return str(value)

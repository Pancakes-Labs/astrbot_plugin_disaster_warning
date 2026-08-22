"""
JMA hypo 英文震央地名 → 中文展示名。

官方 hypo GeoJSON 的 place 字段为英文缩写（如 E OFF FUKUSHIMA PREF）。
映射口径对齐常见中文震央统计（简体 + 「沖/近海/地方」等用语）。

策略：
1. 全量精确映射优先（覆盖实测高频地名）
2. 去掉 REGION/REG 后缀后再查精确表
3. 规则回退（PREF / OFF / NEAR / BORDER 等）
"""

from __future__ import annotations

import re

# 都道府县
_PREF_MAP: dict[str, str] = {
    "HOKKAIDO": "北海道",
    "AOMORI": "青森",
    "IWATE": "岩手",
    "MIYAGI": "宫城",
    "AKITA": "秋田",
    "YAMAGATA": "山形",
    "FUKUSHIMA": "福岛",
    "IBARAKI": "茨城",
    "TOCHIGI": "栃木",
    "GUNMA": "群马",
    "SAITAMA": "埼玉",
    "CHIBA": "千叶",
    "TOKYO": "东京",
    "KANAGAWA": "神奈川",
    "NIIGATA": "新潟",
    "TOYAMA": "富山",
    "ISHIKAWA": "石川",
    "FUKUI": "福井",
    "YAMANASHI": "山梨",
    "NAGANO": "长野",
    "GIFU": "岐阜",
    "SHIZUOKA": "静冈",
    "AICHI": "爱知",
    "MIE": "三重",
    "SHIGA": "滋贺",
    "KYOTO": "京都",
    "OSAKA": "大阪",
    "HYOGO": "兵库",
    "NARA": "奈良",
    "WAKAYAMA": "和歌山",
    "TOTTORI": "鸟取",
    "SHIMANE": "岛根",
    "OKAYAMA": "冈山",
    "HIROSHIMA": "广岛",
    "YAMAGUCHI": "山口",
    "TOKUSHIMA": "德岛",
    "KAGAWA": "香川",
    "EHIME": "爱媛",
    "KOCHI": "高知",
    "FUKUOKA": "福冈",
    "SAGA": "佐贺",
    "NAGASAKI": "长崎",
    "KUMAMOTO": "熊本",
    "OITA": "大分",
    "MIYAZAKI": "宫崎",
    "KAGOSHIMA": "鹿儿岛",
    "OKINAWA": "冲绳",
}

# 方位词（县内分区 + 海域方向）
# 注意：单字母 N/S/E/W 必须存在，供 "E OFF ..." 规则回退使用
_REGION_WORD_MAP: dict[str, str] = {
    "NORTHERN": "北部",
    "SOUTHERN": "南部",
    "EASTERN": "东部",
    "WESTERN": "西部",
    "CENTRAL": "中部",
    "MID": "中部",
    "NORTH": "北部",
    "SOUTH": "南部",
    "EAST": "东部",
    "WEST": "西部",
    "NE": "东北",
    "NW": "西北",
    "SE": "东南",
    "SW": "西南",
    "N": "北",
    "S": "南",
    "E": "东",
    "W": "西",
}

# 精确映射：覆盖实测 JMA hypo 英文地名（样本全集 + 示例对齐）
_EXACT_MAP: dict[str, str] = {
    # ── 海域 / 沖合 ──
    "FAR E OFF SANRIKU": "三陆沖",
    "FAR E OFF NORTH HONSHU": "本州北方远东沖",
    "FAR E OFF CENTRAL HONSHU": "本州中部远东沖",
    "FAR E OFF KANTO": "关东东方沖",
    "FAR E OFF FUKUSHIMA PREF": "福岛县远东沖",
    "FAR E OFF IBARAKI PREF": "茨城县远东沖",
    "FAR E OFF MIYAGI PREF": "宫城县远东沖",
    "FAR E OFF MIYAZAKI PREF": "宫崎县远东沖",
    "FAR E OFF IZU ISLANDS": "伊豆诸岛远东沖",
    "FAR E OFF OGASAWARA": "小笠原远东沖",
    "FAR S OFF BOSO PENINSULA": "房总半岛远南沖",
    "FAR SE OFF BOSO PEN": "房总半岛远东南沖",
    "FAR S OFF ISHIGAKIJIMA": "石垣岛远南沖",
    "FAR S OFF OKINAWAJIMA": "冲绳本岛远南沖",
    "FAR S OFF SHIZUOKA PREF": "静冈县远南沖",
    "FAR S OFF TOKAI DISTRICT": "东海道南方沖",
    "FAR SE OFF HOKKAIDO": "北海道远东南沖",
    "FAR SE OFF KURILE ISL": "千岛群岛远东南海域",
    "E OFF FUKUSHIMA PREF": "福岛县沖",
    "E OFF IWATE PREF": "岩手县沖",
    "NE OFF IWATE PREF": "岩手县沖",
    "E OFF MIYAGI PREF": "宫城县沖",
    "SE OFF MIYAGI PREF": "宫城县沖",
    "E OFF IBARAKI PREF": "茨城县沖",
    "E OFF AOMORI PREF": "青森县东方沖",
    "W OFF AOMORI PREF": "青森县西方沖",
    "E OFF HOKKAIDO": "北海道东方沖",
    "E OFF BOSO PENINSULA": "千叶县东方沖",
    "SE OFF BOSO PENINSULA": "房总半岛南方沖",
    "E OFF HACHIJOJIMA ISLAND": "八丈岛东方沖",
    "E OFF TANEGASHIMA ISLAND": "种子岛东方沖",
    "E OFF IZU PENINSULA": "伊豆半岛东方沖",
    "SE OFF OSUMI PEN": "大隅半岛东方沖",
    "SE OFF TOKACHI": "十胜沖",
    "SE OFF ERIMOMISAKI": "襟裳岬东南沖",
    "SE OFF SHIKOKU": "四国沖",
    "SE OFF KII PENINSULA": "纪伊半岛东南沖",
    "SE OFF ETOROFU": "择捉岛东南沖",
    "S OFF KII PENINSULA": "纪伊半岛南方沖",
    "S OFF URAKAWA": "浦河沖",
    "S OFF TOMAKOMAI": "苫小牧沖",
    "S OFF SHIKOKU": "四国沖",
    "SW OFF HOKKAIDO": "北海道西南沖",
    "SW OFF KYUSHU": "九州西南沖",
    "NW OFF ISHIGAKIJIMA IS": "石垣岛西北沖",
    "NW OFF MIYAKOJIMA ISLAND": "宫古岛西北沖",
    "NW OFF OKINAWAJIMA IS": "冲绳本岛西北沖",
    "NW OFF AMAMI-OSHIMA IS": "奄美大岛西北沖",
    "NW OFF KYUSHU": "九州西北沖",
    "NW OFF HOKKAIDO": "北海道西北沖",
    "NW OFF HOKURIKU DISTRICT": "北陆西北沖",
    "NW OFF KINKI DISTRICT": "近畿西北沖",
    "NW OFF SHAKOTAN PEN": "积丹半岛西北沖",
    "W OFF AMAKUSA ISLAND": "天草滩",
    "W OFF AKITA PREF": "秋田县沖",
    "W OFF YAMAGATA PREF": "山形县沖",
    "W OFF OGASAWARA": "小笠原诸岛西方沖",
    "NE OFF HOKKAIDO": "北海道东北沖",
    "OFF NOTO PENINSULA": "能登半岛沖",
    "OFF NEMURO PENINSULA": "根室半岛东南沖",
    "OFF N NIIGATA PREF": "新潟县上中越沖",
    "OFF S NIIGATA PREF": "新潟县上中越沖",
    "OFF W SAN'IN REGION": "山阴沖",
    "OFF E SAN'IN REGION": "山阴沖",
    "S PART OF KII CHANNEL": "纪伊水道",
    "N PART OF KII CHANNEL": "纪伊水道",
    "E PART OF WAKASA BAY": "若狭湾",
    "W PART OF WAKASA BAY": "若狭湾",
    "S OF SURUGA BAY": "骏河湾南方沖",
    # ── 近海 / 岛屿 ──
    "NEAR TOKARA ISLANDS": "吐噶喇列岛近海",
    "NEAR AMAMI-OSHIMA ISLAND": "奄美大岛近海",
    "NEAR MIYAKOJIMA ISLAND": "宫古岛近海",
    "NEAR OKINAWAJIMA ISLAND": "冲绳本岛近海",
    "NEAR ISHIGAKIJIMA ISLAND": "石垣岛近海",
    "NEAR TANEGASHIMA ISLAND": "种子岛近海",
    "NEAR IZU-OSHIMA ISLAND": "伊豆大岛近海",
    "NEAR NIIJIMA ISLAND": "新岛・神津岛近海",
    "NEAR MIYAKEJIMA ISLAND": "三宅岛近海",
    "NEAR HACHIJOJIMA ISLAND": "八丈岛近海",
    "NEAR CHICHIJIMA ISLAND": "父岛近海",
    "NEAR CHICHIJIMA": "父岛近海",
    "CHICHIJIMA ISLAND": "父岛近海",
    "CHICHIJIMA": "父岛近海",
    "NEAR TORISHIMA IS": "鸟岛近海",
    "NEAR MINAMI-DAITOJIMA IS": "南大东岛近海",
    "NEAR KUNASHIRI ISLAND": "国后岛附近",
    "NEAR ETOROFU ISLAND": "择捉岛附近",
    "NEAR ETOROFU": "择捉岛附近",
    "ETOROFU": "择捉岛附近",
    "NEAR CHOSHI CITY": "千叶县东北部",
    "NEAR KAGOSHIMA CITY": "鹿儿岛县萨摩地方",
    "NEAR UNZENDAKE": "长崎县岛原半岛",
    "NEAR MATSUSHIRO": "长野县北部",
    "IOTO ISLANDS": "硫黄岛近海",
    "IOTO ISLAND": "硫黄岛近海",
    "IOTO ISLANDS REGION": "硫黄岛近海",
    "KURILE ISLANDS": "千岛群岛",
    "KURIL ISLANDS": "千岛群岛",
    "KURILE ISLANDS REGION": "千岛群岛",
    # ── 海峡 / 水道 / 滩 / 湾 ──
    "HYUGANADA REGION": "日向滩",
    "BUNGO CHANNEL": "丰后水道",
    "IYONADA SETONAIKAI": "伊予滩",
    "HIUCHINADA SETONAIKAI": "濑户内海中部",
    "AKINADA SETONAIKAI": "安艺滩",
    "SUONADA SETONAIKAI": "周防滩",
    "HARIMANADA SETONAIKAI": "播磨滩",
    "ENSYUNADA": "远州滩",
    "SAGAMINADA": "相模湾",
    "SAGAMI BAY REGION": "相模湾",
    "TOSA BAY REGION": "土佐湾",
    "MIKAWA BAY REGION": "三河湾",
    "ISE BAY REGION": "伊势湾",
    "SENDAI BAY REGION": "仙台湾",
    "ISHIKARI BAY REGION": "石狩湾",
    "TOYAMA BAY REGION": "富山湾",
    "TOKYO BAY REGION": "东京湾",
    "OSAKA BAY REGION": "大阪湾",
    "UCHIURA BAY REGION": "内浦湾",
    "NORTHERN SURUGA BAY REG": "骏河湾",
    "SOUTHERN SURUGA BAY REG": "骏河湾",
    "NORTHERN ARIAKEKAI REG": "有明海",
    "TSUGARU STRAIT REGION": "津轻海峡",
    # ── 半岛 / 地方专名 ──
    "NOTO PENINSULA REGION": "石川县能登地方",
    "SATSUMA PENINSULA REGION": "鹿儿岛县萨摩地方",
    "SHIMA PENINSULA REGION": "三重县中部",
    "OSUMI PENINSULA REGION": "鹿儿岛县大隅地方",
    "SHIMOKITA PENINSULA REG": "青森县下北地方",
    "OGA PENINSULA REGION": "秋田县沿岸北部",
    "TSUGARU PENINSULA REGION": "青森县津轻北部",
    "OSHIMA PEN REG HOKKAIDO": "渡岛地方东部",
    "SHIRETOKO PENINSULA REG": "根室地方北部",
    "SOUTHERN BOSO PENINSULA": "千叶县南部",
    "CENTRAL IZU PENINSULA": "静冈县伊豆地方",
    "SOUTHERN IZU PENINSULA": "静冈县伊豆地方",
    "HIDA MOUNTAINS REGION": "岐阜县飞驒地方",
    "HIDAKA MOUNTAINS REGION": "日高地方中部",
    "HIDAKA REGION": "日高地方中部",
    "AKAISHI MOUNTAINS REG": "赤石山脉",
    "TAISETSU MOUNTAINS REG": "大雪山",
    "HAKONE REGION": "神奈川县西部",
    "MT. FUJI REGION": "山梨县东部・富士五湖",
    "HAMANAKO LAKE REGION": "静冈县西部",
    "KINKAZAN REGION": "金华山近海",
    "KUJUKURI COAST BOSO PEN": "千叶县东北部",
    "SADOGASHIMA IS REG": "佐渡附近",
    "AWAJISHIMA ISLAND REGION": "淡路岛附近",
    "AMAKUSA REGION": "熊本县天草・芦北地方",
    "EBINO REGION S KYUSHU": "宫崎县北部山沿",
    "KAMIKAWA-SORACHI REGION": "上川地方北部",
    "KUSHIRO REGION": "钏路地方中南部",
    "NEMURO REGION": "根室地方中部",
    "TOKACHI REGION": "十胜地方中部",
    "IBURI REGION": "胆振地方中东部",
    "SOYA REGION": "宗谷地方北部",
    "RUMOI REGION": "留萌地方中北部",
    "ABASHIRI REGION": "网走地方",
    "SHIRIBESHI REGION": "后志地方西部",
    "TESHIKAGA REGION": "钏路地方北部",
    "ISHIKARI DEPRESSION": "石狩地方南部",
    # ── 边界 ──
    "TOCHIGI GUNMA BORDER": "栃木・群马边界",
    "KYOTO OSAKA BORDER REG": "京都・大阪边界",
    "SHIGA GIFU BORDER REGION": "滋贺・岐阜边界",
    "SHIMANE HIROSHIMA BORDER": "岛根县东部",
    "FUKUI GIFU BORDER REGION": "福井・岐阜边界",
    "TOYAMA GIFU BORDER REG": "富山・岐阜边界",
    # ── 县内分区（对齐示例口径）──
    "CENTRAL AICHI PREF": "爱知县西部",
    "NE AICHI PREF": "爱知县东部",
    "NW WAKAYAMA PREF": "和歌山县北部",
    "NE WAKAYAMA PREF": "和歌山县北部",
    "CENTRAL WAKAYAMA PREF": "和歌山县北部",
    "SOUTHERN WAKAYAMA PREF": "和歌山县南部",
    "WESTERN NAGANO PREF": "长野县南部",
    "CENTRAL NAGANO PREF": "长野县中部",
    "NORTHERN NAGANO PREF": "长野县北部",
    "EASTERN NAGANO PREF": "长野县中部",
    "SOUTHERN NAGANO PREF": "长野县南部",
    "EASTERN FUKUSHIMA PREF": "福岛县滨通",
    "MID FUKUSHIMA PREF": "福岛县中通",
    "WESTERN FUKUSHIMA PREF": "福岛县会津",
    "NORTHERN IBARAKI PREF": "茨城县北部",
    "SOUTHERN IBARAKI PREF": "茨城县南部",
    "SW IBARAKI PREF": "茨城县南部",
    "NORTHERN IWATE PREF": "岩手县内陆北部",
    "SOUTHERN IWATE PREF": "岩手县内陆南部",
    "NORTHERN MIYAGI PREF": "宫城县北部",
    "SOUTHERN MIYAGI PREF": "宫城县南部",
    "NORTHERN MIYAZAKI PREF": "宫崎县北部平野部",
    "SOUTHERN MIYAZAKI PREF": "宫崎县南部山沿",
    "NE KUMAMOTO PREF": "熊本县阿苏地方",
    "NW KUMAMOTO PREF": "熊本县熊本地方",
    "SOUTHERN KUMAMOTO PREF": "熊本县球磨地方",
    "SW EHIME PREF": "爱媛县南予",
    "NE EHIME PREF": "爱媛县东予",
    "MID EHIME PREF": "爱媛县中予",
    "CENTRAL EHIME PREF": "爱媛县中予",
    "MID KOCHI PREF": "高知县中部",
    "SE KOCHI PREF": "高知县东部",
    "SW KOCHI PREF": "高知县西部",
    "NORTHERN TOCHIGI PREF": "栃木县北部",
    "SOUTHERN TOCHIGI PREF": "栃木县南部",
    "NORTHERN AKITA PREF": "秋田县内陆北部",
    "SOUTHERN AKITA PREF": "秋田县内陆南部",
    "WESTERN TOTTORI PREF": "鸟取县西部",
    "EASTERN TOTTORI PREF": "鸟取县东部",
    "SOUTHERN NARA PREF": "奈良县",
    "NORTHERN NARA PREF": "奈良县",
    "CENTRAL SHIZUOKA PREF": "静冈县中部",
    "SW SHIZUOKA PREF": "静冈县西部",
    "EASTERN SHIZUOKA PREF": "静冈县东部",
    "SW GIFU PREF": "岐阜县美浓中西部",
    "SE GIFU PREF": "岐阜县美浓东部",
    "NORTHERN GIFU PREF": "岐阜县飞驒地方",
    "SW HYOGO PREF": "兵库县西南部",
    "SE HYOGO PREF": "兵库县东南部",
    "NORTHERN HYOGO PREF": "兵库县北部",
    "NORTHERN MIE PREF": "三重县北部",
    "SOUTHERN MIE PREF": "三重县南部",
    "CENTRAL CHIBA PREF": "千叶县西北部",
    "NORTHERN CHIBA PREF": "千叶县东北部",
    "EASTERN YAMANASHI PREF": "山梨县东部・富士五湖",
    "CENTRAL YAMANASHI PREF": "山梨县中・西部",
    "MID KYOTO PREF": "京都府南部",
    "NORTHERN KYOTO PREF": "京都府北部",
    "CENTRAL FUKUI PREF": "福井县岭北",
    "WESTERN FUKUI PREF": "福井县岭南",
    "MID NIIGATA PREF": "新潟县中越地方",
    "NE NIIGATA PREF": "新潟县下越地方",
    "SW NIIGATA PREF": "新潟县上越地方",
    "EASTERN AOMORI PREF": "青森县三八上北地方",
    "WESTERN AOMORI PREF": "青森县津轻南部",
    "NORTHERN YAMAGATA PREF": "山形县村山地方",
    "SOUTHERN YAMAGATA PREF": "山形县置赐地方",
    "WESTERN HIROSHIMA PREF": "广岛县西南部",
    "EASTERN HIROSHIMA PREF": "广岛县东南部",
    "WESTERN SAITAMA PREF": "埼玉县秩父地方",
    "EASTERN SAITAMA PREF": "埼玉县北部",
    "NW SHIGA PREF": "滋贺县北部",
    "SE SHIGA PREF": "滋贺县南部",
    "NW GUNMA PREF": "群马县北部",
    "SE GUNMA PREF": "群马县南部",
    "NW KAGOSHIMA PREF": "鹿儿岛县萨摩地方",
    "NORTHERN OITA PREF": "大分县北部",
    "SOUTHERN OITA PREF": "大分县南部",
    "NE FUKUOKA PREF": "福冈县北九州地方",
    "CENTRAL FUKUOKA PREF": "福冈县福冈地方",
    "SOUTHERN FUKUOKA PREF": "福冈县筑后地方",
    "NE SHIMANE PREF": "岛根县东部",
    "SW SHIMANE PREF": "岛根县西部",
    # ── 整县 ──
    "TOKUSHIMA PREF": "德岛县北部",
    "YAMAGUCHI PREF": "山口县中部",
    "KAGAWA PREF": "香川县西部",
    "OKAYAMA PREF": "冈山县南部",
    "NAGASAKI PREF": "长崎县西南部",
    "KANAGAWA PREF": "神奈川县西部",
    "TOKYO PREF": "东京都23区",
    "TOYAMA PREF": "富山县西部",
    "ISHIKAWA PREF": "石川县加贺地方",
    "OSAKA PREF": "大阪府北部",
    "SAGA PREF": "佐贺县南部",
    # ── 海外 / 远地 ──
    "TAIWAN REGION": "中国台湾地区",
    "S KOREAN PENINSULA REG": "韩国",
    "SOUTH SAKHALIN": "俄罗斯萨哈林州",
    "EASTERN SEA OF JAPAN": "日本海东部",
    "SEA OF JAPAN": "日本海",
    "SOUTHERN SEA OF OKHOTSK": "鄂霍次克海",
    "EAST CHINA SEA REGION": "东中国海",
    "N PHILIPPINE BASIN": "菲律宾海盆",
    "PHILIPPINE ISL REGION": "菲律宾群岛地区",
    "PHILIPPINE ISLANDS REGION": "菲律宾群岛地区",
    "E OFF PHILIPPINE ISLANDS": "菲律宾群岛东方沖",
    "E OFF PHILIPPINE ISL": "菲律宾群岛东方沖",
    "PHILIPPINE ISLANDS E OFF": "菲律宾群岛东方沖",
    "PHILIPPINE ISL E OFF": "菲律宾群岛东方沖",
    "MARIANA ISLANDS REGION": "马里亚纳群岛地区",
    "NORTH PACIFIC": "北太平洋",
    "SHIKOKU BASIN": "四国海盆",
    "GOTO ISLANDS REGION": "五岛列岛近海",
    "OKINOSHIMA ISLAND REGION": "隐岐岛近海",
    # ── 远地 / 邻国补充（实测残留英文）──
    "VLADIVOSTOK": "符拉迪沃斯托克近海",
    "NEAR VLADIVOSTOK": "符拉迪沃斯托克近海",
    "VLADIVOSTOK REGION": "符拉迪沃斯托克近海",
    "VLADIVOSTOK NEAR": "符拉迪沃斯托克近海",
    "FARFIELD": "远地",
    "FAR FIELD": "远地",
    "FAR-FIELD": "远地",
    "YELLOWSEA": "黄海",
    "YELLOW SEA": "黄海",
    "YELLOW SEA REGION": "黄海",
    "SOUTHERNSIBERIA": "西伯利亚南部",
    "SOUTHERN SIBERIA": "西伯利亚南部",
    "S SIBERIA": "西伯利亚南部",
    "SOUTH SIBERIA": "西伯利亚南部",
}


def _norm_key(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().upper())


def _title_pref(name: str) -> str:
    key = name.strip().upper()
    return _PREF_MAP.get(key, name.title())


def _replace_prefs(text: str) -> str:
    out = text
    for en, cn in sorted(_PREF_MAP.items(), key=lambda x: -len(x[0])):
        out = re.sub(rf"\b{en}\b", cn, out)
    return out


def _translate_by_rules(place: str) -> str:
    text = _norm_key(place)
    if not text:
        return "未知地点"

    # FAR? DIR OFF target
    # 长匹配优先，避免 NE 被 N 抢先切分
    m = re.fullmatch(
        r"(FAR\s+)?(NE|NW|SE|SW|N|S|E|W)\s+OFF\s+(.+)",
        text,
    )
    if m:
        far = "远" if m.group(1) else ""
        direction = _REGION_WORD_MAP.get(m.group(2), m.group(2))
        target = m.group(3).strip()
        target = target.replace(" PENINSULA", "半岛").replace(" PEN", "半岛")
        target = target.replace(" ISLANDS", "诸岛").replace(" ISLAND", "岛")
        target = target.replace(" ISL", "诸岛").replace(" IS", "岛")
        target = re.sub(r"\bPREF\b", "县", target)
        target = re.sub(r"\bDISTRICT\b", "", target).strip()
        target = _replace_prefs(target)
        specials = {
            "SANRIKU": "三陆",
            "KANTO": "关东",
            "HONSHU": "本州",
            "KYUSHU": "九州",
            "SHIKOKU": "四国",
            "HOKKAIDO": "北海道",
            "OSUMI": "大隅",
            "BOSO": "房总",
            "KII": "纪伊",
            "IZU": "伊豆",
            "TOKACHI": "十胜",
            "ETOROFU": "择捉岛",
            "URAKAWA": "浦河",
            "TOMAKOMAI": "苫小牧",
            "OGASAWARA": "小笠原",
            "ISHIGAKIJIMA": "石垣岛",
            "OKINAWAJIMA": "冲绳本岛",
            "MIYAKOJIMA": "宫古岛",
            "AMAMI-OSHIMA": "奄美大岛",
            "TANEGASHIMA": "种子岛",
            "HACHIJOJIMA": "八丈岛",
            "KURILE": "千岛",
            "HOKURIKU": "北陆",
            "KINKI": "近畿",
            "TOKAI": "东海",
            "SAN'IN": "山阴",
            "SHAKOTAN": "积丹",
        }
        for en, cn in specials.items():
            target = target.replace(en, cn)
        target = re.sub(r"\s+", "", target)
        if any(
            x in target
            for x in (
                "县",
                "半岛",
                "岛",
                "三陆",
                "关东",
                "本州",
                "浦河",
                "苫小牧",
                "十胜",
            )
        ):
            dir_short = direction.replace("远", "")
            return f"{target}{far}{dir_short}沖".replace("远远", "远")
        return f"{far}{direction}{target}沖".replace("远远", "远")

    # OFF X
    m = re.fullmatch(r"OFF\s+(.+)", text)
    if m:
        target = m.group(1)
        target = target.replace(" PENINSULA", "半岛").replace(" PEN", "半岛")
        target = re.sub(r"\bPREF\b", "县", target)
        target = _replace_prefs(target)
        target = re.sub(r"\s+", "", target)
        return f"{target}沖"

    # NEAR X
    m = re.fullmatch(r"NEAR\s+(.+)", text)
    if m:
        target = m.group(1)
        target = target.replace(" ISLANDS", "诸岛").replace(" ISLAND", "岛")
        target = target.replace(" ISL", "诸岛").replace(" IS", "岛")
        target = target.replace(" CITY", "")
        specials = {
            "TOKARA": "吐噶喇列岛",
            "AMAMI-OSHIMA": "奄美大岛",
            "MIYAKOJIMA": "宫古岛",
            "OKINAWAJIMA": "冲绳本岛",
            "ISHIGAKIJIMA": "石垣岛",
            "TANEGASHIMA": "种子岛",
            "IZU-OSHIMA": "伊豆大岛",
            "NIIJIMA": "新岛・神津岛",
            "MIYAKEJIMA": "三宅岛",
            "HACHIJOJIMA": "八丈岛",
            "CHICHIJIMA": "父岛",
            "TORISHIMA": "鸟岛",
            "MINAMI-DAITOJIMA": "南大东岛",
            "KUNASHIRI": "国后岛",
            "ETOROFU": "择捉岛",
            "CHOSHI": "千叶县东北部",
            "KAGOSHIMA": "鹿儿岛",
            "UNZENDAKE": "长崎县岛原半岛",
            "MATSUSHIRO": "长野县北部",
        }
        for en, cn in specials.items():
            if en in target:
                if cn.endswith(("近海", "附近", "地方", "部")):
                    return cn
                if "岛" in cn or "列岛" in cn:
                    return f"{cn}近海" if not cn.endswith("附近") else cn
                return f"{cn}近海"
        target = _replace_prefs(target)
        target = re.sub(r"\s+", "", target)
        return f"{target}近海"

    # DIR X PREF
    m = re.fullmatch(
        r"(NORTHERN|SOUTHERN|EASTERN|WESTERN|CENTRAL|MID|NORTH|SOUTH|EAST|WEST|NE|NW|SE|SW)\s+(.+?)\s+PREF",
        text,
    )
    if m:
        region = _REGION_WORD_MAP.get(m.group(1), m.group(1))
        pref = _title_pref(m.group(2))
        return f"{pref}县{region}"

    # X PREF
    m = re.fullmatch(r"(.+?)\s+PREF", text)
    if m:
        return f"{_title_pref(m.group(1))}县"

    # X BORDER
    m = re.fullmatch(r"(.+?)\s+BORDER(?:\s+REG(?:ION)?)?", text)
    if m:
        body = _replace_prefs(m.group(1))
        body = re.sub(r"\s+", "・", body.strip())
        return f"{body}边界"

    # X REGION / X REG
    m = re.fullmatch(r"(.+?)\s+(REGION|REG)", text)
    if m:
        body = m.group(1)
        body = body.replace(" PENINSULA", "半岛").replace(" MOUNTAINS", "山脉")
        body = body.replace(" BAY", "湾").replace(" ISLANDS", "诸岛")
        body = body.replace(" ISLAND", "岛").replace(" STRAIT", "海峡")
        body = body.replace(" LAKE", "湖")
        body = _replace_prefs(body)
        specials = {
            "HYUGANADA": "日向滩",
            "NOTO半岛": "石川县能登地方",
            "SATSUMA半岛": "鹿儿岛县萨摩地方",
            "HIDA山脉": "岐阜县飞驒地方",
            "SOYA": "宗谷地方北部",
            "TOKACHI": "十胜地方中部",
            "HIDAKA": "日高地方中部",
            "IBURI": "胆振地方中东部",
            "KUSHIRO": "钏路地方中南部",
            "NEMURO": "根室地方中部",
            "RUMOI": "留萌地方中北部",
            "ABASHIRI": "网走地方",
            "AMAKUSA": "熊本县天草・芦北地方",
            "TAIWAN": "中国台湾地区",
            "MARIANA诸岛": "马里亚纳群岛地区",
            "PHILIPPINE ISL": "菲律宾群岛地区",
            "GOTO诸岛": "五岛列岛近海",
            "SEA OF JAPAN": "日本海",
            "EAST CHINA SEA": "东中国海",
        }
        compact = re.sub(r"\s+", "", body)
        for en, cn in specials.items():
            if en in compact or en in body:
                return cn
        if compact.endswith(("滩", "湾", "海峡", "水道", "地方", "近海")):
            return compact
        return f"{compact}地方"

    # 无空格粘连 / 远地补充
    compact_key = re.sub(r"[\s\-_]+", "", text)
    compact_specials = {
        "VLADIVOSTOK": "符拉迪沃斯托克近海",
        "VLADIVOSTOKNEAR": "符拉迪沃斯托克近海",
        "NEARVLADIVOSTOK": "符拉迪沃斯托克近海",
        "FARFIELD": "远地",
        "YELLOWSEA": "黄海",
        "SOUTHERNSIBERIA": "西伯利亚南部",
        "SOUTHSIBERIA": "西伯利亚南部",
        "SSIBERIA": "西伯利亚南部",
        "PHILIPPINEISLANDSEOFF": "菲律宾群岛东方沖",
        "PHILIPPINEISLEOFF": "菲律宾群岛东方沖",
        "EOFFPHILIPPINEISLANDS": "菲律宾群岛东方沖",
        "EOFFPHILIPPINEISL": "菲律宾群岛东方沖",
    }
    if compact_key in compact_specials:
        return compact_specials[compact_key]
    for en, cn in compact_specials.items():
        if en in compact_key:
            return cn

    # 通用回退
    out = text
    out = _replace_prefs(out)
    repl = {
        "PENINSULA": "半岛",
        "PEN": "半岛",
        "ISLANDS": "诸岛",
        "ISLAND": "岛",
        "ISL": "诸岛",
        "CHANNEL": "水道",
        "BAY": "湾",
        "STRAIT": "海峡",
        "REGION": "地方",
        "REG": "地方",
        "PREF": "县",
        "DISTRICT": "",
        "MOUNTAINS": "山脉",
        "OFF": "沖",
        "NEAR": "近海",
        "BORDER": "边界",
        "SEA OF JAPAN": "日本海",
        "YELLOW SEA": "黄海",
        "OKHOTSK": "鄂霍次克",
        "PACIFIC": "太平洋",
        "TAIWAN": "台湾",
        "KURILE": "千岛",
        "MARIANA": "马里亚纳",
        "PHILIPPINE": "菲律宾",
        "VLADIVOSTOK": "符拉迪沃斯托克",
        "SIBERIA": "西伯利亚",
        "FARFIELD": "远地",
        "YELLOWSEA": "黄海",
    }
    for en, cn in sorted(repl.items(), key=lambda x: -len(x[0])):
        out = out.replace(en, cn)
    out = re.sub(r"\s+", "", out)
    return out or place


def translate_jma_hypo_place(place: str | None) -> str:
    """将 JMA hypo 英文地名翻译为中文展示名。"""
    raw = str(place or "").strip()
    if not raw:
        return "未知地点"

    key = _norm_key(raw)
    if key in _EXACT_MAP:
        return _EXACT_MAP[key]

    # 去掉 REGION/REG 后再匹配
    key2 = re.sub(r"\s+(REGION|REG)$", "", key).strip()
    if key2 in _EXACT_MAP:
        return _EXACT_MAP[key2]

    # 去掉常见前缀噪声后再匹配
    key3 = re.sub(r"^(NEAR|OFF)\s+", "", key2).strip()
    if key3 in _EXACT_MAP:
        return _EXACT_MAP[key3]

    return _translate_by_rules(raw)


__all__ = ["translate_jma_hypo_place"]

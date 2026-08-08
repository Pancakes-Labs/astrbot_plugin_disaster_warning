(() => {
    // 震度数值与对应的界面圆角徽标背景色映射表
    const INT_COLOR_MAP = {
        '1': '#6B7878',
        '2': '#1E6EE6',
        '3': '#32B464',
        '4': '#FFE05D',
        '5-': '#FFAA13',
        '5+': '#EF700F',
        '6-': '#E60000',
        '6+': '#A00000',
        '7': '#5D0090',
        unknown: '#6B7878',
    };

    /**
     * 健壮地将数据库内扁平的时间戳字符串转化为前端兼容的 ISO 8601 标准 UTC 时间戳格式
     */
    function normalizeDbUtcTime(rawTime) {
        if (!rawTime) return '';
        const text = String(rawTime).trim();
        if (!text) return '';
        // 如果格式如 2026-05-22 18:00:00，则自动将空格替换为 T 字符并追加 Z 零时区标识
        if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(text)) {
            return `${text.replace(' ', 'T')}Z`;
        }
        return text;
    }

    /**
     * 获取事件展示所需的核心时间戳
     * 
     * 判定逻辑：
     * - 可选择优先使用事件记录更新时间（recorded_at）还是发震发灾的最初物理时间（time）。
     */
    function getDisplayTimeValue(event, preferUpdateTime = false) {
        if (!event || typeof event !== 'object') return '';
        const updateTime = normalizeDbUtcTime(event.recorded_at || event.updated_at || event.timestamp);
        const eventTime = event.time || event.timestamp || '';
        return preferUpdateTime ? (updateTime || eventTime || '') : (eventTime || updateTime || '');
    }

    /**
     * 判断数据源是否为日本气象厅或相关高时效的特定数据节点
     */
    function isLikelyJmaSource(source = '') {
        const sourceKey = String(source || '').toLowerCase();
        if (!sourceKey) return false;
        return sourceKey.includes('jma') || sourceKey.includes('p2p') || sourceKey.includes('cwa');
    }

    /**
     * 净化及归一化地震卡片的标题和地名
     * 
     * 净化策略：
     * - 剥离标题首部冗余的“M 级”震级标识。
     * - 拦截调查中的地震事件，对日本源事件标记为震度速报，对其他源标记为参数调查中。
     */
    function normalizeEarthquakeTitle(evt) {
        const rawTitle = String(evt?.description || '').trim();
        if (!rawTitle) return '未知位置';

        const magPrefixMatch = rawTitle.match(/^M\s*([^\s]+)\s*(.*)$/i);
        if (magPrefixMatch) {
            const [, magTokenRaw, restRaw] = magPrefixMatch;
            const magToken = String(magTokenRaw || '').toLowerCase();
            const rest = String(restRaw || '').trim();
            const invalidMagToken = ['none', 'nan', '--', 'null', 'undefined'].includes(magToken);
            const unknownPlace = !rest || rest === '未知地点' || rest === '未知位置';
            
            // 匹配到无地点无震级的调查中异常状态事件
            if (invalidMagToken && unknownPlace) {
                return isLikelyJmaSource(evt?.source) ? '震度速报（震源参数调查中）' : '震源参数调查中';
            }
            if (rest) return rest;
        }
        return rawTitle;
    }

    /**
     * 格式化震级为保留一位小数的标准格式
     */
    function formatMagnitudeBadge(mag) {
        if (mag === null || mag === undefined || mag === '') return '--';
        const num = Number(mag);
        return Number.isFinite(num) ? num.toFixed(1) : '--';
    }

    /**
     * 格式化气象厅 Shindo 震度级别
     * 
     * 转换策略：
     * - 自动将日文的 弱 强 关键字替换为标准的减号与加号。
     * - 对传入的连续浮点数进行范围划档，对齐到 [1, 2, 3, 4, 5-, 5+, 6-, 6+, 7] 的等级上。
     */
    function formatShindoBadge(level) {
        if (level === null || level === undefined || level === '') return null;
        const raw = String(level).trim();
        if (!raw) return null;
        // 规整字符
        const normalized = raw.replace(/弱/g, '-').replace(/強/g, '+').replace(/强/g, '+').replace(/\s+/g, '');
        if (['1', '2', '3', '4', '5-', '5+', '6-', '6+', '7'].includes(normalized)) return normalized;
        
        // 浮点数分档算法
        const num = Number(level);
        if (!Number.isFinite(num)) return null;
        if (num < 1.5) return '1';
        if (num < 2.5) return '2';
        if (num < 3.5) return '3';
        if (num < 4.5) return '4';
        if (num < 5.0) return '5-';
        if (num < 5.5) return '5+';
        if (num < 6.0) return '6-';
        if (num < 6.5) return '6+';
        return '7';
    }

    /**
     * 格式化中国标准的地震烈度级别
     */
    function formatIntensityBadge(level) {
        if (level === null || level === undefined || level === '') return null;
        const num = Number(level);
        if (!Number.isFinite(num)) return null;
        const rounded = Math.round(num);
        // 如果是标准的 1 至 12 度，则输出整型字符，否则输出保留一位的小数
        if (rounded >= 1 && rounded <= 12) return String(rounded);
        return num.toFixed(1);
    }

    /**
     * 根据震级或烈度值及所处度量体系获取对应的色盘颜色代码
     */
    function getIntensityColor(levelText, isJmaScale) {
        if (!levelText) return INT_COLOR_MAP.unknown;
        // 日本气象厅震度
        if (isJmaScale) return INT_COLOR_MAP[levelText] || INT_COLOR_MAP.unknown;
        
        // 中国标准烈度级别划分判色
        const n = Number(levelText);
        if (!Number.isFinite(n)) return INT_COLOR_MAP.unknown;
        if (n <= 2) return INT_COLOR_MAP['1'];
        if (n <= 4) return INT_COLOR_MAP['2'];
        if (n <= 5) return INT_COLOR_MAP['3'];
        if (n <= 6) return INT_COLOR_MAP['4'];
        if (n <= 8) return INT_COLOR_MAP['5-'];
        if (n <= 10) return INT_COLOR_MAP['6-'];
        return INT_COLOR_MAP['7'];
    }

    /**
     * 拼装样式类名后缀，用于在前台激活对应的阴影发光或底色 CSS
     */
    function normalizeBadgeToneToken(levelText, isJmaScale) {
        if (!levelText) return 'unknown';
        if (isJmaScale) {
            const normalized = String(levelText).trim().replace(/\s+/g, '');
            const jmaToneMap = {
                '1': 'shindo-1',
                '2': 'shindo-2',
                '3': 'shindo-3',
                '4': 'shindo-4',
                '5-': 'shindo-5-minus',
                '5+': 'shindo-5-plus',
                '6-': 'shindo-6-minus',
                '6+': 'shindo-6-plus',
                '7': 'shindo-7',
            };
            return jmaToneMap[normalized] || 'unknown';
        }
        
        const n = Number(levelText);
        if (!Number.isFinite(n)) return 'unknown';
        if (n <= 2) return 'intensity-1';
        if (n <= 4) return 'intensity-2';
        if (n <= 5) return 'intensity-3';
        if (n <= 6) return 'intensity-4';
        if (n <= 8) return 'intensity-5-minus';
        if (n <= 10) return 'intensity-6-minus';
        return 'intensity-7';
    }

    /**
     * 综合获取地震类事件卡片侧边圆圈高亮徽标的文本、状态类名及字样
     */
    function getEarthquakeBadgeContent(evt) {
        const source = evt?.source || '';
        const level = evt?.level;
        const isJmaScale = isLikelyJmaSource(source);
        if (isJmaScale) {
            const shindo = formatShindoBadge(level);
            if (shindo) {
                return { text: shindo, label: '震度', toneClass: normalizeBadgeToneToken(shindo, true) };
            }
        } else {
            const intensity = formatIntensityBadge(level);
            if (intensity) {
                return { text: intensity, label: '烈度', toneClass: normalizeBadgeToneToken(intensity, false) };
            }
        }
        // 退化分支：若无震度或烈度，则展示常规 M 级震级
        return { text: formatMagnitudeBadge(evt?.magnitude ?? evt?._groupMagnitude), label: '震级', toneClass: 'unknown' };
    }

    /**
     * 快速构建出符合规范的地震大标题，形如：M 6.0 四川雅安
     */
    function buildEarthquakeTitle(evt) {
        const normalizedTitle = normalizeEarthquakeTitle(evt);
        if (!normalizedTitle) return '未知位置';
        if (normalizedTitle.includes('调查中')) return normalizedTitle;
        const magText = formatMagnitudeBadge(evt?.magnitude ?? evt?._groupMagnitude);
        if (magText === '--') return normalizedTitle;
        return `M ${magText} ${normalizedTitle}`;
    }

    // ---- 海啸列表标题 / 元信息（兼容升级前旧记录）----

    const JP_TSUNAMI_LEVEL_DISPLAY = {
        Minor: '若干海面变动',
        Watch: '海啸注意报',
        Warning: '海啸警报',
        MajorWarning: '大海啸警报',
        None: '海啸预报',
        Unknown: '海啸预报',
        解除: '海啸解除',
    };

    const CN_TSUNAMI_COLORS = ['红色', '橙色', '黄色', '蓝色'];

    function cleanTsunamiText(value) {
        const text = String(value ?? '').trim();
        if (!text) return '';
        const lowered = text.toLowerCase();
        if (['null', 'none', 'unknown', '未知', '未知地点', '未知位置'].includes(lowered)) {
            return '';
        }
        return text;
    }

    function isGenericTsunamiTitle(text) {
        const cleaned = cleanTsunamiText(text);
        if (!cleaned) return true;
        const generics = new Set([
            '海啸信息', '海啸情报', '海啸预警', '海啸警报', '海啸解除', '海啸解除通告',
            '津波予報', '津波注意報', '津波警報', '大津波警報', '津波予報（解除）',
            '若干の海面変動', '若干海面变动', '海啸注意报', '大海啸警报',
        ]);
        if (generics.has(cleaned)) return true;
        if (
            cleaned.startsWith('海啸')
            && /信息|警报|预警|解除|注意报/.test(cleaned)
            && !cleaned.includes('·')
            && !/[Mm]j?\s*[\d.]/.test(cleaned)
            && cleaned.length <= 12
        ) {
            return true;
        }
        return false;
    }

    function isLegacyTsunamiDescription(description, level) {
        const text = cleanTsunamiText(description);
        if (!text) return true;
        if (text.includes(' (') && text.endsWith(')')) {
            const idx = text.lastIndexOf(' (');
            const head = text.slice(0, idx).trim();
            const levelPart = text.slice(idx + 2, -1).trim();
            if (head && levelPart) {
                if (levelPart && head.includes(levelPart)) return true;
                if (
                    head === '海啸信息'
                    || head === `海啸${levelPart}`
                    || head === `海啸${levelPart}警报`
                ) {
                    return true;
                }
                const levelText = cleanTsunamiText(level);
                if (levelText && levelPart === levelText) return true;
            }
        }
        return isGenericTsunamiTitle(text);
    }

    function resolveTsunamiRegion(evt) {
        const source = String(evt?.source_id || evt?.source || '').toLowerCase();
        const infoType = String(evt?.info_type || '').toLowerCase();
        if (
            infoType.includes('jma')
            || source.includes('jma')
            || source.includes('p2p')
            || source.includes('eqsc')
            || source.includes('japan')
        ) {
            return 'japan';
        }
        if (
            infoType.includes('cn')
            || source.includes('fan')
            || source.includes('china')
            || source.includes('tsunami_fan')
        ) {
            return 'china';
        }
        const level = cleanTsunamiText(evt?.level);
        if (['Minor', 'Watch', 'Warning', 'MajorWarning', 'None', 'Unknown'].includes(level)) {
            return 'japan';
        }
        if (level === '信息' || CN_TSUNAMI_COLORS.includes(level) || level === '解除') {
            return 'china';
        }
        return 'unknown';
    }

    function formatTsunamiLevelLabel(evt) {
        const cancelled = Boolean(
            evt?.is_cancelled
            || evt?.cancelled
            || cleanTsunamiText(evt?.level) === '解除'
            || String(evt?.description || '').includes('解除')
        );
        if (cancelled) return '海啸解除';

        const region = resolveTsunamiRegion(evt);
        const level = cleanTsunamiText(evt?.level);

        if (region === 'japan') {
            if (JP_TSUNAMI_LEVEL_DISPLAY[level]) return JP_TSUNAMI_LEVEL_DISPLAY[level];
            const lower = level.toLowerCase();
            const map = {
                minor: '若干海面变动',
                watch: '海啸注意报',
                warning: '海啸警报',
                majorwarning: '大海啸警报',
            };
            if (map[lower]) return map[lower];
            return level || '海啸预报';
        }

        if (level === '信息') return '海啸信息';
        if (CN_TSUNAMI_COLORS.includes(level)) return `海啸${level}警报`;
        if (level === '解除') return '海啸解除';

        // 从 description / title 提取颜色
        const haystack = `${evt?.description || ''} ${evt?.subtitle || ''}`;
        for (const color of CN_TSUNAMI_COLORS) {
            if (haystack.includes(color)) return `海啸${color}警报`;
        }
        if (haystack.includes('信息')) return '海啸信息';
        if (level) return level.startsWith('海啸') ? level : `海啸${level}`;
        return '海啸情报';
    }

    function formatTsunamiMagnitudeToken(evt) {
        const raw = evt?.magnitude;
        if (raw === null || raw === undefined || raw === '') return '';
        const num = Number(raw);
        if (!Number.isFinite(num)) return '';
        const magText = Number.isInteger(num) ? `${num}.0` : String(Number(num.toFixed(1)));
        const region = resolveTsunamiRegion(evt);
        return region === 'japan' ? `Mj${magText}` : `M${magText}`;
    }

    function resolveTsunamiPlaceName(evt) {
        const candidates = [
            evt?.place_name,
            evt?.placeName,
            evt?.subtitle,
        ];
        for (const item of candidates) {
            const text = cleanTsunamiText(item);
            if (text && !isGenericTsunamiTitle(text)) return text;
        }
        // 旧 description 若已是「级别 · 地点 Mxx」可截取地点
        const desc = cleanTsunamiText(evt?.description);
        if (desc && desc.includes('·')) {
            const parts = desc.split('·').map((p) => p.trim()).filter(Boolean);
            if (parts.length >= 2) {
                // 去掉末尾震级
                let place = parts[1].replace(/\s*M[jJ]?\s*[\d.]+$/, '').trim();
                place = place.replace(/（第.+?）$/, '').trim();
                if (place && !isGenericTsunamiTitle(place)) return place;
            }
        }
        return '';
    }

    /**
     * 从文本中剥离内嵌报数标记，例如「（第3报）」「(第 3 报)」。
     * 列表标题旁已有独立报数徽章，避免标题与徽章两套语义打架。
     */
    function stripEmbeddedReportToken(text) {
        const raw = String(text || '').trim();
        if (!raw) return '';
        return raw
            .replace(/[（(]\s*第\s*\d+\s*报\s*[）)]/g, '')
            .replace(/\s{2,}/g, ' ')
            .replace(/\s*·\s*$/g, '')
            .trim();
    }

    /**
     * 从 description / weather_detail / batch 字段尽力解析业务报次。
     */
    function extractReportNumFromText(text) {
        const raw = String(text || '').trim();
        if (!raw) return null;
        const match = raw.match(/第\s*(\d+)\s*[报批]/)
            || raw.match(/批次\s*(\d+)/)
            || raw.match(/(?:^|[^\d])(\d+)\s*报/);
        if (!match) return null;
        const num = Number(match[1]);
        return Number.isInteger(num) && num > 0 ? num : null;
    }

    /**
     * 从事件对象提取业务 batch 报次（不含系统 update_count）。
     * 纯数字限制 1～999，避免时间戳/事件 ID 被误当成报次。
     */
    function resolveBusinessBatchNum(evt) {
        if (!evt || typeof evt !== 'object') return null;
        const MAX_BUSINESS_BATCH = 999;
        const batchCandidates = [
            evt.batch,
            evt.Batch,
            evt.weather_detail,
            // 旧 description 可能内嵌「（第3报）」；新标题已剥离，但仍兼容历史数据
            evt.description,
            evt.subtitle,
        ];
        for (const candidate of batchCandidates) {
            if (candidate === null || candidate === undefined || candidate === '') continue;
            const asNum = Number(candidate);
            if (Number.isInteger(asNum) && asNum >= 1 && asNum <= MAX_BUSINESS_BATCH) {
                return asNum;
            }
            const parsed = extractReportNumFromText(candidate);
            if (parsed && parsed >= 1 && parsed <= MAX_BUSINESS_BATCH) return parsed;
        }
        return null;
    }

    /**
     * 统一解析事件展示用报次。
     *
     * 语义：
     * - 地震等：report_num 即业务报次
     * - 海啸：report_num 应为业务 batch；旧数据可能被错误写成 update_count，
     *   因此海啸优先读 batch / weather_detail 中的业务报次，再回退 report_num
     * - update_count 只表示系统合并次数，用于「N 条更新」，不进徽章
     *
     * 优先级：显式 override >（海啸业务 batch）> report_num > 文本回退
     */
    function resolveEventReportNum(evt, override = null) {
        if (override !== null && override !== undefined && override !== '') {
            const forced = Number(override);
            if (Number.isInteger(forced) && forced > 0) return forced;
        }
        if (!evt || typeof evt !== 'object') return null;

        const eventType = String(evt.type || evt._groupType || '').toLowerCase();
        const isTsunami = eventType === 'tsunami';

        if (isTsunami) {
            const businessBatch = resolveBusinessBatchNum(evt);
            if (businessBatch) return businessBatch;
        }

        const direct = Number(evt.report_num);
        if (Number.isInteger(direct) && direct > 0) return direct;

        if (!isTsunami) {
            const fallbackBatch = resolveBusinessBatchNum(evt);
            if (fallbackBatch) return fallbackBatch;
        }
        return null;
    }

    /**
     * 格式化「第 N 报」展示文案。
     */
    function formatReportLabel(reportNum) {
        const num = Number(reportNum);
        if (!Number.isInteger(num) || num <= 0) return '';
        return `第 ${num} 报`;
    }

    /**
     * 构建海啸列表主标题。
     * 优先用后端新 description；若是旧「海啸信息 (信息)」则用结构化字段重拼。
     * 注意：标题内不再附带「（第N报）」，报次统一由卡片徽章展示，避免与 report_num 冲突。
     */
    function buildTsunamiTitle(evt) {
        if (!evt || typeof evt !== 'object') return '海啸情报';

        const description = stripEmbeddedReportToken(cleanTsunamiText(evt.description));
        const level = cleanTsunamiText(evt.level);
        const place = resolveTsunamiPlaceName(evt);
        const magToken = formatTsunamiMagnitudeToken(evt);
        const isTraining = Boolean(evt.is_training || evt.isTraining);

        // 新 description 已经可读：直接用
        if (description && !isLegacyTsunamiDescription(description, level)) {
            // 训练标记兜底
            if (isTraining && !description.includes('[训练]') && !description.includes('训练')) {
                return `[训练] ${description}`;
            }
            return description;
        }

        const levelLabel = formatTsunamiLevelLabel(evt);
        let head = levelLabel;
        if (isTraining && !head.startsWith('[训练]')) {
            head = `[训练] ${head}`;
        }

        const body = [];
        if (place) {
            body.push(magToken ? `${place} ${magToken}` : place);
        } else if (magToken) {
            body.push(magToken);
        }

        // 无地点/震级时补波高或预报区（新字段；旧数据可能为空）
        if (!body.length) {
            const waveRaw = evt.max_wave_height;
            if (waveRaw !== null && waveRaw !== undefined && waveRaw !== '') {
                const waveNum = Number(waveRaw);
                if (Number.isFinite(waveNum) && waveNum > 0) {
                    body.push(`最大波高 ${waveNum}m`);
                } else {
                    const waveText = cleanTsunamiText(waveRaw);
                    if (waveText) body.push(`最大波高 ${waveText}`);
                }
            }
            const areaCount = Number(evt.area_count);
            if (!body.length && Number.isFinite(areaCount) && areaCount > 0) {
                body.push(`预报区 ${areaCount}`);
            }
        }

        if (body.length) {
            return `${head} · ${body.join(' · ')}`;
        }
        return head || '海啸情报';
    }

    /**
     * 解析 weather_detail 中的结构化片段（新入库摘要）。
     * 例：预报区 8，立即到达 2，最大波高 3m（福島県），监测站 4，
     *     级别分布 海啸警报 3 / 海啸注意报 5，重点预报 ...，监测实况 ...
     */
    function parseTsunamiWeatherDetail(detailText) {
        const detail = cleanTsunamiText(detailText);
        const result = {
            regionLabel: '',
            areaCount: null,
            immediateCount: null,
            stationCount: null,
            maxWaveText: '',
            maxWaveArea: '',
            gradeDistribution: '',
            forecastHighlights: [],
            stationHighlights: [],
            depthText: '',
            batchText: '',
            raw: detail,
        };
        if (!detail) return result;

        if (detail.includes('日本海啸')) result.regionLabel = '日本';
        else if (detail.includes('中国海啸')) result.regionLabel = '中国';

        const areaMatch = detail.match(/预报区\s*(\d+)/);
        if (areaMatch) result.areaCount = Number(areaMatch[1]);

        const immediateMatch = detail.match(/立即到达\s*(\d+)/);
        if (immediateMatch) result.immediateCount = Number(immediateMatch[1]);

        const stationMatch = detail.match(/监测站\s*(\d+)/);
        if (stationMatch) result.stationCount = Number(stationMatch[1]);

        const waveMatch = detail.match(/最大波高\s*([^，,]+)/);
        if (waveMatch) {
            const waveChunk = waveMatch[1].trim();
            const areaInParen = waveChunk.match(/（([^）]+)）|\(([^)]+)\)/);
            if (areaInParen) {
                result.maxWaveArea = (areaInParen[1] || areaInParen[2] || '').trim();
                result.maxWaveText = waveChunk.replace(/（[^）]+）|\([^)]+\)/g, '').trim();
            } else {
                result.maxWaveText = waveChunk;
            }
        }

        const gradeMatch = detail.match(/级别分布\s*([^，,]+)/);
        if (gradeMatch) result.gradeDistribution = gradeMatch[1].trim();

        const forecastMatch = detail.match(/重点预报\s*([^，,]+)/);
        if (forecastMatch) {
            result.forecastHighlights = forecastMatch[1]
                .split(/[；;]/)
                .map((item) => item.trim())
                .filter(Boolean);
        }

        const stationHighlightMatch = detail.match(/监测实况\s*([^，,]+)/);
        if (stationHighlightMatch) {
            result.stationHighlights = stationHighlightMatch[1]
                .split(/[；;]/)
                .map((item) => item.trim())
                .filter(Boolean);
        }

        const depthMatch = detail.match(/深度\s*([\d.]+)\s*km/i);
        if (depthMatch) result.depthText = `${depthMatch[1]}km`;

        const batchMatch = detail.match(/批次\s*([^\s，,]+)/);
        if (batchMatch) result.batchText = batchMatch[1];

        return result;
    }

    function resolveTsunamiLevelTone(evt) {
        const cancelled = Boolean(
            evt?.is_cancelled
            || evt?.cancelled
            || cleanTsunamiText(evt?.level) === '解除'
            || String(evt?.description || '').includes('解除')
        );
        if (cancelled) return 'cancel';
        const level = cleanTsunamiText(evt?.level);
        const haystack = `${level} ${evt?.description || ''} ${evt?.weather_detail || ''}`;
        if (/MajorWarning|大海啸|红色/.test(haystack)) return 'major';
        if (/Warning|海啸警报|橙色/.test(haystack) && !/注意/.test(haystack)) return 'warning';
        if (/Watch|注意报|黄色/.test(haystack)) return 'watch';
        if (/蓝色/.test(haystack)) return 'blue';
        if (/Minor|若干|信息|预报/.test(haystack)) return 'info';
        return 'default';
    }

    /**
     * 海啸卡片结构化元信息（对齐推送展示器语义）。
     * 返回 chips + 重点预报/监测摘要；旧数据字段缺失时自动降级。
     */
    function buildTsunamiMeta(evt) {
        if (!evt || typeof evt !== 'object') {
            return { chips: [], sections: [], text: '' };
        }

        const parsed = parseTsunamiWeatherDetail(evt.weather_detail);
        const chips = [];
        const sections = [];
        const levelLabel = formatTsunamiLevelLabel(evt);
        const tone = resolveTsunamiLevelTone(evt);
        const place = resolveTsunamiPlaceName(evt);
        const magToken = formatTsunamiMagnitudeToken(evt);
        const region = resolveTsunamiRegion(evt);

        const pushChip = (key, icon, label, chipTone = 'default') => {
            if (!label) return;
            chips.push({ key, icon, label, tone: chipTone });
        };

        if (levelLabel) pushChip('level', '📋', levelLabel, tone);
        if (parsed.regionLabel) pushChip('region', '🌏', parsed.regionLabel, 'default');
        else if (region === 'japan') pushChip('region', '🌏', '日本', 'default');
        else if (region === 'china') pushChip('region', '🌏', '中国', 'default');

        if (place) pushChip('place', '🌍', place, 'place');
        if (magToken) pushChip('mag', '🧭', magToken, 'mag');

        const depthRaw = evt.depth;
        if (depthRaw !== null && depthRaw !== undefined && depthRaw !== '') {
            const depthNum = Number(depthRaw);
            // 合法深度 >= 0；负数（调查中占位）不展示；0 映射为极浅
            // 文案与 EventCard 地震芯片统一为「Nkm / 极浅」
            if (Number.isFinite(depthNum) && depthNum >= 0) {
                const depthText = depthNum === 0
                    ? '极浅'
                    : `${Number.isInteger(depthNum) ? depthNum : depthNum}km`;
                pushChip('depth', '⬇️', `深度 ${depthText}`, 'default');
            }
        } else if (parsed.depthText) {
            const parsedDepthNum = Number(String(parsed.depthText).replace(/km/i, ''));
            if (Number.isFinite(parsedDepthNum) && parsedDepthNum >= 0) {
                const depthText = parsedDepthNum === 0
                    ? '极浅'
                    : `${Number.isInteger(parsedDepthNum) ? parsedDepthNum : parsedDepthNum}km`;
                pushChip('depth', '⬇️', `深度 ${depthText}`, 'default');
            }
        }

        // 波高：结构化字段优先，再回退 weather_detail
        let waveLabel = '';
        const waveRaw = evt.max_wave_height;
        if (waveRaw !== null && waveRaw !== undefined && waveRaw !== '') {
            const waveNum = Number(waveRaw);
            if (Number.isFinite(waveNum) && waveNum > 0) {
                waveLabel = `最大波高 ${waveNum}m`;
            } else {
                const waveText = cleanTsunamiText(waveRaw);
                if (waveText) waveLabel = `最大波高 ${waveText}`;
            }
        }
        if (!waveLabel && parsed.maxWaveText) {
            waveLabel = `最大波高 ${parsed.maxWaveText}`;
        }
        if (waveLabel && parsed.maxWaveArea) {
            waveLabel = `${waveLabel}（${parsed.maxWaveArea}）`;
        }
        if (waveLabel) pushChip('wave', '🌊', waveLabel, 'wave');

        const areaCount = Number(evt.area_count);
        if (Number.isFinite(areaCount) && areaCount > 0) {
            pushChip('areas', '📍', `预报区 ${areaCount}`, 'area');
        } else if (parsed.areaCount) {
            pushChip('areas', '📍', `预报区 ${parsed.areaCount}`, 'area');
        }

        const immediate = Number(evt.immediate_area_count);
        if (Number.isFinite(immediate) && immediate > 0) {
            pushChip('immediate', '🚨', `立即到达 ${immediate}`, 'danger');
        } else if (parsed.immediateCount) {
            pushChip('immediate', '🚨', `立即到达 ${parsed.immediateCount}`, 'danger');
        }

        if (parsed.stationCount) {
            pushChip('stations', '📡', `监测站 ${parsed.stationCount}`, 'station');
        }

        // 报次统一由卡片标题旁徽章展示，chip 区不再重复「第N报」，避免与 report_num 冲突。

        if (evt.is_cancelled || evt.cancelled || cleanTsunamiText(evt.level) === '解除') {
            pushChip('cancelled', '✅', '已解除', 'cancel');
        }
        if (evt.is_training || evt.isTraining) {
            pushChip('training', '🧪', '训练报', 'training');
        }

        if (parsed.gradeDistribution) {
            sections.push({
                key: 'grade',
                icon: '📊',
                title: '级别分布',
                body: parsed.gradeDistribution,
                tone: 'grade',
            });
        }

        if (parsed.forecastHighlights.length) {
            // 日本：津波予報区域；中国：沿海预报
            const forecastTitle = region === 'japan'
                ? `津波予報区域（${parsed.forecastHighlights.length}）`
                : `沿海预报（${parsed.forecastHighlights.length}）`;
            sections.push({
                key: 'forecasts',
                icon: region === 'japan' ? '📍' : '',
                title: forecastTitle,
                items: parsed.forecastHighlights,
                tone: 'forecast',
            });
        }

        // 监测实况：中国源常见；日本通常无监测站列表，有才展示
        if (parsed.stationHighlights.length) {
            sections.push({
                key: 'stations',
                icon: '📡',
                title: `监测实况（${parsed.stationHighlights.length}）`,
                items: parsed.stationHighlights,
                tone: 'station',
            });
        }

        // 旧数据几乎无结构化字段时，展示原始 weather_detail 兜底
        if (!chips.length && !sections.length) {
            const detail = cleanTsunamiText(evt.weather_detail);
            if (detail) {
                sections.push({
                    key: 'fallback',
                    icon: '📝',
                    title: '摘要',
                    body: detail,
                    tone: 'default',
                });
            }
        }

        const text = [
            ...chips.map((chip) => chip.label),
            ...sections.map((section) => {
                if (section.items && section.items.length) {
                    return `${section.title}：${section.items.join('；')}`;
                }
                return section.body ? `${section.title}：${section.body}` : section.title;
            }),
        ].filter(Boolean).join(' · ');

        return { chips, sections, text, tone };
    }

    /**
     * 时间轴节点短标题（等级）
     */
    function buildTsunamiTimelineTitle(evt) {
        return formatTsunamiLevelLabel(evt) || '海啸预警';
    }

    /**
     * 时间轴副标题（地点优先）
     */
    function buildTsunamiTimelineSubtitle(evt) {
        const place = resolveTsunamiPlaceName(evt);
        if (place) return place;
        const title = buildTsunamiTitle(evt);
        if (title.length > 14) return `${title.slice(0, 14)}…`;
        return title || '海啸';
    }

    /**
     * 格式化并友好汉化下拉框中的数据源选项
     */
    function normalizeSourceOption(item) {
        if (!item) return null;
        const sourceValue = String(item.source_value || '').trim();
        const sourceLabel = String(item.source_label || '').trim();
        const rawValue = sourceValue || sourceLabel;
        if (!rawValue) return null;
        return {
            value: rawValue,
            label: formatSourceName(sourceLabel || sourceValue || rawValue), // 友好汉化地名名称
            normalizedKey: normalizeSourceName(rawValue),
        };
    }

    /**
     * 批量清洗汉化数据源过滤项，并按拼音降序排列
     */
    function normalizeSourceOptions(sourceOptions) {
        return (Array.isArray(sourceOptions) ? sourceOptions : [])
            .map(normalizeSourceOption)
            .filter(Boolean)
            .sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'));
    }

    /**
     * 根据气象预警编码解析颜色关键词。
     *
     * 支持编码格式：
     * - 新格式 11B20_yellow：下划线后即为颜色关键词（统一转小写以兼容 _Yellow / _YELLOW）
     * - 旧格式 p0002002：最后一位数字表示颜色（1=红, 2=橙, 3=黄, 4=蓝）
     * - 紧凑 11B 格式 11B3102 / 11B2002：末两位 01/02/03/04 表示蓝/黄/橙/红
     *
     * @param {string} weatherTypeCode  气象预警编码
     * @returns {string|null}           颜色关键词，如 'red' / 'yellow' / 'orange' / 'blue'
     */
    function resolveWeatherColor(weatherTypeCode) {
        const P_FORMAT_MAP = {
            '1': 'red',
            '2': 'orange',
            '3': 'yellow',
            '4': 'blue',
        };
        // 与事件 ID 尾部紧凑编码一致：01蓝 02黄 03橙 04红
        const COMPACT_11B_MAP = {
            '01': 'blue',
            '02': 'yellow',
            '03': 'orange',
            '04': 'red',
        };
        const VALID_COLORS = new Set(['blue', 'yellow', 'orange', 'red']);
        const code = String(weatherTypeCode || '').trim();
        if (!code) return null;

        if (code.includes('_')) {
            const color = code.split('_').pop().toLowerCase();
            return VALID_COLORS.has(color) ? color : null;
        }
        if (code.startsWith('p') && code.length >= 8) {
            return P_FORMAT_MAP[code.slice(-1)] || null;
        }
        // 仅当编码是 11B/11E 格式且末两位明确是 01/02/03/04 时才认作颜色。
        // 必须限定前缀，否则 p 码（如 p0000003）末两位 03 会被误判成橙色。
        if (
            (code.startsWith('11B') || code.startsWith('11E'))
            && code.length >= 2
        ) {
            const color = COMPACT_11B_MAP[code.slice(-2)];
            if (color) return color;
        }
        return null;
    }

    // 与后端 weather_alarm_code_map._P_TYPE_TO_11B_BASE 对齐：p 编码 4 位类型前缀 → 11B 基础码
    const P_TYPE_TO_11B_BASE = {
        '0001': '11B01',  // 台风
        '0002': '11B03',  // 暴雨
        '0003': '11B09',  // 高温
        '0004': '11B05',  // 寒潮
        '0005': '11B17',  // 大雾
        '0006': '11B04',  // 暴雪
        '0007': '11B06',  // 大风
        '0008': '11B07',  // 沙尘暴
        '0009': '11B15',  // 冰雹
        '0010': '11B22',  // 干旱
        '0011': '11B21',  // 道路结冰
        '0012': '11B14',  // 雷电
        '0013': '11B16',  // 霜冻
        '0014': '11B19',  // 霾
        '0015': '11B20',  // 雷雨大风
    };

    // p 编码末位颜色数字 → 颜色关键词（与 resolveWeatherColor 内 P_FORMAT_MAP 一致）
    const P_CODE_COLOR_MAP = {
        '1': 'red',
        '2': 'orange',
        '3': 'yellow',
        '4': 'blue',
    };

    // 紧凑 11B 编码末两位颜色码 → 颜色关键词（与后端 _COMPACT_11B_COLOR_TO_SUFFIX 一致）
    const COMPACT_11B_COLOR_MAP = {
        '01': 'blue',
        '02': 'yellow',
        '03': 'orange',
        '04': 'red',
    };

    /**
     * 将气象预警编码解析为本地图标文件名对应的 11B 完整码（本地优先）。
     *
     * 与后端 weather_alarm_code_map.resolve_weather_icon_code 对齐：
     * - 11B/11E 完整码（含下划线新格式）直接规范化返回；
     * - 紧凑 11B 编码（如 11B2001）标准化为 11B20_blue 后返回；
     * - p 编码按 4 位类型码 + 末位颜色数字转换为 11B 完整码。
     * 前端无标题/摘要上下文，因此不执行标题兜底解析。
     *
     * @param {string} weatherTypeCode  气象预警编码，如 "p0002003"、"11B03_yellow" 或 "11B2001"
     * @returns {string|null}           11B 完整码，如 "11B03_yellow"；无法解析返回 null
     */
    function resolveWeatherIconCode(weatherTypeCode) {
        const code = String(weatherTypeCode || '').trim();
        if (!code) return null;

        // 1. 已是 11B/11E 完整码（含下划线格式）：规范化颜色小写后直接返回
        if (/^(11B|11E)\d+_[A-Za-z]+$/.test(code)) {
            const base = code.slice(0, code.indexOf('_'));
            const normalizedColor = code.slice(code.indexOf('_') + 1).toLowerCase();
            if (base && normalizedColor) return `${base}_${normalizedColor}`;
        }

        // 2. 紧凑 11B 编码（11B2001 → 11B20_blue）：末两位为颜色码
        if (/^(11B|11E)\d{3,}$/.test(code) && code.length >= 4) {
            const base = code.slice(0, -2);
            const color = COMPACT_11B_COLOR_MAP[code.slice(-2)];
            if (base && color) return `${base}_${color}`;
        }

        // 3. p 编码：4 位类型码 + 末位颜色数字
        if (code.startsWith('p') && code.length >= 8) {
            const digits = code.slice(1);
            const typePart = digits.slice(0, 4);
            const colorDigit = digits.slice(-1);
            const base = P_TYPE_TO_11B_BASE[typePart];
            const color = P_CODE_COLOR_MAP[colorDigit];
            if (base && color) return `${base}_${color}`;
        }

        return null;
    }

    /**
     * 解析本地具体预警图标 URL（本地优先核心）。
     *
     * 根据气象预警编码解析出 11B 完整码后，映射为本地静态资源 URL：
     * /weatheralarm_logo/{11B码}.png（如 /weatheralarm_logo/11B03_yellow.png）。
     * 无法解析编码时返回 null，由调用方回退到通用 fallback 图标。
     *
     * @param {string} weatherTypeCode  气象预警编码
     * @returns {string|null}           本地图标 URL，如 /weatheralarm_logo/11B03_yellow.png
     */
    function resolveLocalWeatherIconUrl(weatherTypeCode) {
        const iconCode = resolveWeatherIconCode(weatherTypeCode);
        return iconCode ? `/weatheralarm_logo/${iconCode}.png` : null;
    }

    /**
     * 根据气象预警编码解析本地回退图标 URL。
     *
     * @param {string} weatherTypeCode  气象预警编码
     * @returns {string|null}           本地回退图标路径，如 /weatheralarm_logo/fallback_red.png
     */
    function resolveWeatherFallbackUrl(weatherTypeCode) {
        const COLOR_MAP = {
            blue: 'fallback_blue.png',
            yellow: 'fallback_yellow.png',
            orange: 'fallback_orange.png',
            red: 'fallback_red.png',
        };
        const color = resolveWeatherColor(weatherTypeCode);
        const fallbackFile = color ? COLOR_MAP[color] : null;
        return fallbackFile ? `/weatheralarm_logo/${fallbackFile}` : null;
    }

    /**
     * 判断图片元素加载结果是否为无效内容。
     *
     * Fan Studio 图标接口对不存在的编码会返回 HTTP 200 的 HTML 伪图片，
     * 浏览器 <img> 不会触发 onError，但加载的内容不是有效图片。
     * 通过 naturalWidth/naturalHeight 是否为 0 来识别这种伪图片。
     *
     * @param {HTMLImageElement} el  图片元素
     * @returns {boolean}  true 表示图片无效（需回退）
     */
    function isWeatherImageInvalid(el) {
        if (!el || typeof el.naturalWidth !== 'number') return false;
        return el.naturalWidth === 0 || el.naturalHeight === 0;
    }

    /**
     * 构建气象预警图标 img onError/onLoad 回退处理器（本地优先）。
     *
     * 触发时机：
     * - onError：图片加载失败（404 等）
     * - onLoad：图片加载成功但内容无效（如 Fan Studio 返回 HTTP 200 的 HTML 伪图片，
     *   naturalWidth=0，onError 不会触发但 onLoad 会触发）
     *
     * 回退顺序：
     * 1. 本地具体预警图标：/weatheralarm_logo/{11B码}.png（如 11B03_yellow.png）；
     * 2. 本地通用回退图标：/weatheralarm_logo/fallback_{color}.png；
     * 3. data-color-hint 提示色解析的本地通用回退图标；
     * 4. finalCallback 最终兜底。
     *
     * @param {string} weatherTypeCode  气象预警编码，如 "11B20_yellow" 或 "p0002002"
     * @param {Function} finalCallback  最终兜底回调，接收事件对象 e
     * @returns {Function}              可直接绑定到 img onError/onLoad 的处理器
     */
    function buildWeatherIconFallbackHandler(weatherTypeCode, finalCallback) {
        return function (e) {
            const el = e.currentTarget;
            const code = String(weatherTypeCode || '').trim();

            // onLoad 场景：若图片内容无效（伪图片/空图），继续走 fallback 链
            if (e.type === 'load' && !isWeatherImageInvalid(el)) {
                return; // 有效图片，无需回退
            }

            // 1. 本地具体预警图标（本地优先）
            if (code && !el.dataset.localIconTried) {
                const localUrl = resolveLocalWeatherIconUrl(code);
                if (localUrl) {
                    el.dataset.localIconTried = 'true';
                    el.src = localUrl;
                    return;
                }
            }

            // 2. 本地通用回退图标（按编码解析颜色）
            if (!el.dataset.fallbackTried) {
                const fallbackUrl = code
                    ? resolveWeatherFallbackUrl(code)
                    : null;
                if (fallbackUrl) {
                    el.dataset.fallbackTried = 'true';
                    el.src = fallbackUrl;
                    return;
                }
            }

            // 3. 当 weatherTypeCode 无法解析颜色时，尝试从 data-color-hint 属性获取颜色回退
            if (!el.dataset.fallbackTried) {
                const colorHint = el.dataset.colorHint;
                if (colorHint) {
                    const fallbackUrl = resolveWeatherFallbackUrl(`_${colorHint}`);
                    if (fallbackUrl) {
                        el.dataset.fallbackTried = 'true';
                        el.src = fallbackUrl;
                        return;
                    }
                }
            }

            if (typeof finalCallback === 'function') {
                finalCallback(e);
            }
        };
    }

    window.EventFormatters = {
        INT_COLOR_MAP,
        normalizeDbUtcTime,
        getDisplayTimeValue,
        isLikelyJmaSource,
        normalizeEarthquakeTitle,
        formatMagnitudeBadge,
        formatShindoBadge,
        formatIntensityBadge,
        getIntensityColor,
        normalizeBadgeToneToken,
        getEarthquakeBadgeContent,
        buildEarthquakeTitle,
        stripEmbeddedReportToken,
        extractReportNumFromText,
        resolveBusinessBatchNum,
        resolveEventReportNum,
        formatReportLabel,
        isGenericTsunamiTitle,
        isLegacyTsunamiDescription,
        resolveTsunamiRegion,
        formatTsunamiLevelLabel,
        buildTsunamiTitle,
        buildTsunamiMeta,
        parseTsunamiWeatherDetail,
        resolveTsunamiLevelTone,
        buildTsunamiTimelineTitle,
        buildTsunamiTimelineSubtitle,
        normalizeSourceOption,
        normalizeSourceOptions,
        resolveWeatherColor,
        resolveWeatherIconCode,
        resolveLocalWeatherIconUrl,
        resolveWeatherFallbackUrl,
        isWeatherImageInvalid,
        buildWeatherIconFallbackHandler,
    };
})();

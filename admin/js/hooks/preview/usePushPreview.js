/**
 * @file usePushPreview.js
 * @description 实时推文预览 Hook。
 *
 * 抽象复用能力：把"数据源示例数据 + 当前配置草稿"生成实时推文预览的
 * 逻辑抽成共享 Hook，供配置管理页（左侧编辑 → 右侧实时预览）与
 * 模拟预警页（单步预览）复用。
 *
 * 核心能力：
 * - 拉取模拟 Schema（全灾种 × 全源参数，含默认示例数据）
 * - 数据源按钮滑条：按灾种分组，组内按 schema 排序（中国 → 台湾 → 日本 → 全球）
 * - 500ms 防抖 + 请求竞态保护：快速编辑配置时只发最后一次请求
 * - 返回 preview_text / decision / media_notice / loading / error
 */
(function () {
    const { useState, useEffect, useRef, useCallback } = React;

    const PREVIEW_DEBOUNCE_MS = 500;
    // 最短加载动画时长：避免结果瞬间渲染导致的闪烁，强制展示至少 1s 加载态
    const MIN_LOADING_MS = 1000;

    function usePushPreview({
        runtimeConfig,          // 编辑中的配置草稿（对象，变化触发重新预览）
        targetSession = '',     // 目标会话（可选）
        initialSourceId = '',   // 初始选中的数据源（可选）
        enabled = true,         // 是否启用自动预览（如 schema 未就绪时）
        refreshToken = 0,       // 强制刷新令牌（配置引用相同时字段已变也可触发）
        scopeKey = '',          // 配置作用域键（mode+会话组合）：变化时清空旧结果
    }) {
        const simApi = window.DisasterSimulationApi;

        // Schema 与数据源列表
        const [schema, setSchema] = useState(null);
        const [schemaLoading, setSchemaLoading] = useState(false);

        // 数据源滑条状态
        const [selectedSourceId, setSelectedSourceId] = useState(initialSourceId);

        // 预览结果状态
        const [preview, setPreview] = useState(null); // { preview_text, decision, media_notice, has_images, ... }
        const [loading, setLoading] = useState(false);
        const [error, setError] = useState('');
        // 错误来源类型：'' | 'schema' | 'preview' | 'source'
        // 用于让调用方按错误来源展示对应的重试动作（schema 重载 / 预览重试）
        const [errorKind, setErrorKind] = useState('');

        // 请求时序保护：只有最新一次请求允许写入状态
        const previewSeqRef = useRef(0);
        const debounceTimerRef = useRef(null);
        // 上次作用域键：变化时作废旧结果（多会话/全局-会话切换隔离）
        const prevScopeKeyRef = useRef(scopeKey);
        // 数据源切换时"已立即触发预览"的标记：防抖 effect 跳过紧随其后的
        // 重复请求（selectSource 已即时发起，避免同一 sourceId 发两次 /simulation/preview）
        const immediateFiredSourceRef = useRef('');

        // Schema 重试令牌：瞬时加载失败后可重新拉取 Schema（独立于预览重试）
        const [schemaRetryToken, setSchemaRetryToken] = useState(0);

        /**
         * 初始化：拉取模拟 Schema（含全部数据源默认示例参数）。
         * 依赖 schemaRetryToken：瞬时失败后可通过 reloadSchema 重新拉取，
         * 避免 Schema 加载失败后预览永久无法恢复。
         */
        useEffect(() => {
            let cancelled = false;
            setSchemaLoading(true);
            (async () => {
                try {
                    const result = await simApi.getSchema();
                    if (cancelled) return;
                    setSchema(result);
                    // 默认选中首个灾种的首个数据源
                    if (!initialSourceId) {
                        const firstType = Object.keys(result?.disaster_types || {})[0];
                        const firstSource = result?.disaster_types?.[firstType]?.sources?.[0];
                        if (firstSource) {
                            setSelectedSourceId(firstSource.source_id);
                        }
                    }
                } catch (e) {
                    if (!cancelled) {
                        setError('加载数据源列表失败: ' + (e.message || e));
                        setErrorKind('schema');
                    }
                } finally {
                    if (!cancelled) setSchemaLoading(false);
                }
            })();
            return () => { cancelled = true; };
            // eslint-disable-next-line react-hooks/exhaustive-deps
        }, [schemaRetryToken]);

        /**
         * 根据选中的数据源，从 schema 提取该源的示例参数（字段默认值）
         */
        const getSourceSampleParams = useCallback((sourceId) => {
            const types = schema?.disaster_types || {};
            for (const typeKey of Object.keys(types)) {
                const source = (types[typeKey].sources || []).find(s => s.source_id === sourceId);
                if (source) {
                    const params = {};
                    (source.fields || []).forEach((f) => {
                        if (f.default !== undefined && f.key && !['report_num', 'event_key', 'is_final'].includes(f.key)) {
                            params[f.key] = f.default;
                        }
                    });
                    return { disaster_type: typeKey, source, params };
                }
            }
            return null;
        }, [schema]);

        /**
         * 实际发起预览请求（不防抖，由外部调用方控制节奏）
         */
        const firePreview = useCallback(async (sourceId, config, session) => {
            const seq = ++previewSeqRef.current;
            setLoading(true);
            setError('');
            setErrorKind('');
            // 记录本次请求起始时刻，保证加载动画最短展示 MIN_LOADING_MS
            const startedAt = Date.now();
            try {
                const sample = getSourceSampleParams(sourceId);
                if (!sample) {
                    if (seq === previewSeqRef.current) {
                        setPreview(null);
                        setError('未找到该数据源的参数定义');
                        setErrorKind('source');
                    }
                    return;
                }
                const result = await simApi.preview({
                    disaster_type: sample.disaster_type,
                    source_id: sourceId,
                    params: sample.params,
                    runtime_config: config || null,
                    target_session: session || '',
                });
                if (seq !== previewSeqRef.current) return; // 过期响应丢弃
                // 结果就绪但加载时长未达最短值 → 补齐等待，避免闪烁
                const elapsed = Date.now() - startedAt;
                if (elapsed < MIN_LOADING_MS) {
                    await new Promise((resolve) => setTimeout(resolve, MIN_LOADING_MS - elapsed));
                }
                if (seq !== previewSeqRef.current) return; // 等待期间可能有新请求
                setPreview(result);
            } catch (e) {
                if (seq === previewSeqRef.current) {
                    setPreview(null);
                    setError('预览生成失败: ' + (e.message || e));
                    setErrorKind('preview');
                }
            } finally {
                if (seq === previewSeqRef.current) {
                    setLoading(false);
                }
            }
        }, [getSourceSampleParams, simApi]);

        /**
         * 防抖预览：runtimeConfig 或数据源变化时，500ms 后触发
         */
        useEffect(() => {
            // 作用域切换（切换会话 / 全局↔会话）：立即作废旧结果与挂起请求，
            // 并清除"已立即触发"标记，让当前输入也能进入防抖调度。
            // 切换瞬间 runtimeConfig/targetSession 可能仍是上一作用域的旧值，
            // 但新草稿到达后 effect 会再次触发并以最新输入覆盖，预览不会卡空。
            if (scopeKey !== prevScopeKeyRef.current) {
                prevScopeKeyRef.current = scopeKey;
                previewSeqRef.current += 1; // 使旧请求失效
                if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
                immediateFiredSourceRef.current = '';
                setPreview(null);
                setError('');
                setErrorKind('');
                setLoading(false);
            }
            if (!enabled || !selectedSourceId || !schema || schemaLoading) return;
            if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
            // 数据源切换已在 selectSource 中即时触发预览，跳过本次防抖，避免重复请求
            if (immediateFiredSourceRef.current === selectedSourceId) {
                immediateFiredSourceRef.current = '';
                return;
            }
            debounceTimerRef.current = setTimeout(() => {
                firePreview(selectedSourceId, runtimeConfig, targetSession);
            }, PREVIEW_DEBOUNCE_MS);
            return () => {
                if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
            };
        }, [enabled, selectedSourceId, runtimeConfig, targetSession, schemaLoading, schema, refreshToken, scopeKey, firePreview]);

        /**
         * 手动切换数据源（滑条点击时立即触发，无需等防抖）
         */
        const selectSource = useCallback((sourceId) => {
            if (sourceId === selectedSourceId) return;
            // 标记"已立即触发"，让紧随其后的防抖 effect 跳过，避免重复请求
            immediateFiredSourceRef.current = sourceId;
            setSelectedSourceId(sourceId);
            // 数据源切换立即预览（不等防抖窗口）
            firePreview(sourceId, runtimeConfig, targetSession);
        }, [selectedSourceId, runtimeConfig, targetSession, firePreview]);

        /**
         * 把 schema 中的 disaster_types 拍平为"数据源按钮列表"（含灾种信息）
         * 顺序参考模拟预警：按灾种分组，组内按 schema 排序（后端已按地区排好）
         */
        const sourceList = React.useMemo(() => {
            const types = schema?.disaster_types || {};
            const list = [];
            Object.entries(types).forEach(([typeKey, typeData]) => {
                (typeData.sources || []).forEach((source) => {
                    list.push({
                        typeKey,
                        typeLabel: typeData.label || typeKey,
                        typeIcon: typeData.icon || '',
                        region: source.region || 'global',
                        regionLabel: source.region_label || '全球',
                        sourceId: source.source_id,
                        label: source.label || source.source_id,
                        familyLabel: source.family_label || '',
                    });
                });
            });
            return list;
        }, [schema]);

        // Schema 独立重试：仅重新拉取 Schema 并重置预览错误，不重复预览请求
        const reloadSchema = useCallback(() => {
            setError('');
            setErrorKind('');
            setSchemaRetryToken((prev) => prev + 1);
        }, []);

        return {
            schema,
            schemaLoading,
            sourceList,
            selectedSourceId,
            selectSource,
            preview,
            loading,
            error,
            errorKind,
            retry: () => firePreview(selectedSourceId, runtimeConfig, targetSession),
            reloadSchema,
        };
    }

    window.usePushPreview = usePushPreview;
})();

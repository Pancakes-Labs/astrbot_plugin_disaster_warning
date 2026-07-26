(function (global) {
    "use strict";

    /**
     * 统一的 Leaflet 地图完成判定逻辑。
     * 目标：在低延时前提下，尽量避免截图时出现瓦片未完整落屏。
     *
     * @param {L.Map} map Leaflet 地图实例
     * @param {L.TileLayer} tileLayer Leaflet 瓦片层实例
     * @param {Object} [options] 可选参数
     * @param {number} [options.readyDebounceMs=120] 收敛防抖时间（毫秒）
     * @param {number} [options.readyFallbackMs=2200] 超时兜底时间（毫秒）
     * @param {string} [options.readyClass='map-ready'] 完成后添加到 body 的 class
     * @param {boolean} [options.debug=false] 是否输出调试日志
     * @param {function(string):void} [options.onReady] 完成回调
     * @returns {{destroy: function(): void, isReady: function(): boolean}}
     */
    function setupStableTileRender(map, tileLayer, options) {
        if (!map || !tileLayer) {
            throw new Error("setupStableTileRender requires both map and tileLayer");
        }

        var cfg = Object.assign(
            {
                readyDebounceMs: 120,
                readyFallbackMs: 2200,
                readyClass: "map-ready",
                debug: false,
                onReady: null,
            },
            options || {}
        );

        var pendingTiles = 0;
        var sawTileRequest = false;
        var readyMarked = false;
        var settleTimer = null;
        var fallbackTimer = null;

        function log(msg) {
            if (cfg.debug) {
                console.log(msg);
            }
        }

        function markReady(reason) {
            if (readyMarked) {
                return;
            }
            readyMarked = true;
            document.body.classList.add(cfg.readyClass);
            if (typeof cfg.onReady === "function") {
                cfg.onReady(reason);
            }
            log("[Map Ready] " + reason);
        }

        function isTileLayerLoading() {
            // Leaflet TileLayer 提供 isLoading()；用于覆盖“监听挂上前已加载完”
            // 与“尚未观测到 tile 事件但层仍在加载”两类竞态。
            try {
                return typeof tileLayer.isLoading === "function" && tileLayer.isLoading();
            } catch (_err) {
                return false;
            }
        }

        function scheduleSettle(reason) {
            if (readyMarked) {
                return;
            }
            if (settleTimer) {
                clearTimeout(settleTimer);
            }
            settleTimer = setTimeout(function () {
                // 不再强制要求 sawTileRequest：
                // 台风等模板会在 setupStableTileRender 之前 addTo(map)，
                // 瓦片可能在监听器挂上前就已加载完；若仍要求 sawTileRequest，
                // 会一直拖到 readyFallbackMs 才打 map-ready，导致 Playwright 10s 等待误报超时。
                //
                // 同时用 tileLayer.isLoading() 兜底：慢网环境下首批 tileloadstart
                // 尚未被观测到时，避免仅凭 pendingTiles===0 过早 markReady。
                if (pendingTiles > 0 || isTileLayerLoading()) {
                    return;
                }
                map.invalidateSize({ pan: false, debounceMoveend: true });
                requestAnimationFrame(function () {
                    requestAnimationFrame(function () {
                        markReady(reason);
                    });
                });
            }, cfg.readyDebounceMs);
        }

        var onTileLoadStart = function () {
            sawTileRequest = true;
            pendingTiles += 1;
        };

        var onTileLoad = function () {
            pendingTiles = Math.max(0, pendingTiles - 1);
            scheduleSettle("tile-load");
        };

        var onTileError = function () {
            pendingTiles = Math.max(0, pendingTiles - 1);
            scheduleSettle("tile-error");
        };

        var onLayerLoad = function () {
            sawTileRequest = true;
            pendingTiles = 0;
            scheduleSettle("layer-load");
        };

        tileLayer.on("tileloadstart", onTileLoadStart);
        tileLayer.on("tileload", onTileLoad);
        tileLayer.on("tileerror", onTileError);
        tileLayer.on("load", onLayerLoad);

        map.whenReady(function () {
            setTimeout(function () {
                map.invalidateSize({ pan: false, debounceMoveend: true });
                scheduleSettle("map-ready");
            }, 0);
        });

        fallbackTimer = setTimeout(function () {
            if (!readyMarked) {
                map.invalidateSize({ pan: false, debounceMoveend: true });
                markReady("fallback-timeout");
            }
        }, cfg.readyFallbackMs);

        return {
            destroy: function () {
                if (settleTimer) {
                    clearTimeout(settleTimer);
                }
                if (fallbackTimer) {
                    clearTimeout(fallbackTimer);
                }
                tileLayer.off("tileloadstart", onTileLoadStart);
                tileLayer.off("tileload", onTileLoad);
                tileLayer.off("tileerror", onTileError);
                tileLayer.off("load", onLayerLoad);
            },
            isReady: function () {
                return readyMarked;
            },
        };
    }

    global.setupStableTileRender = setupStableTileRender;
})(typeof window !== "undefined" ? window : this);

/**
 * 模块名称：数据备份与管理面板
 * 功能描述：在全局配置下显示数据导入导出与备份还原的控制界面。
 */

const { Box, Typography, Button, Paper, CircularProgress, Divider, Switch, FormControlLabel, Dialog, DialogTitle, DialogContent, DialogActions, FormGroup, Checkbox } = MaterialUI;
const { useState } = React;

function ConfigBackupPanel() {
    const { showToast } = useToast();
    const [exporting, setExporting] = useState(false);
    const [importing, setImporting] = useState(false);
    const [mergeSessions, setMergeSessions] = useState(true);

    // 弹窗相关状态
    const [dialogOpen, setDialogOpen] = useState(false);
    const [confirmDialogOpen, setConfirmDialogOpen] = useState(false);
    const [pendingFile, setPendingFile] = useState(null);
    const [backupTargets, setBackupTargets] = useState({
        db: true,
        sessions: true,
        stats: true,
        caches: true,
        simulations: true,
        notifications: true,
        logs: true
    });

    // 触发导出备份（支持自定义选项）
    const handleExportFull = async () => {
        const targets = Object.keys(backupTargets).filter(k => backupTargets[k]);
        if (targets.length === 0) {
            showToast('请至少选择一个需要备份的模块！', 'error');
            return;
        }

        setExporting(true);
        setDialogOpen(false);
        try {
            const blob = await window.DisasterConfigApi.exportFullBackup(targets);
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `disaster_warning_backup_${new Date().toISOString().slice(0, 10)}.zip`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
            showToast('备份包导出成功！', 'success');
        } catch (err) {
            console.error(err);
            showToast(`备份导出失败: ${err.message || err}`, 'error');
        } finally {
            setExporting(false);
        }
    };

    // 触发导入完整备份
    const handleImportFullClick = (e) => {
        const file = e.target.files[0];
        if (!file) return;
        setPendingFile(file);
        setConfirmDialogOpen(true);
        // 清空 input 的 value 以便用户可以选择同一个文件触发 onChange
        e.target.value = '';
    };

    const handleConfirmImport = () => {
        if (!pendingFile) return;
        setConfirmDialogOpen(false);
        setImporting(true);
        
        window.DisasterConfigApi.importFullBackup(pendingFile)
            .then((res) => {
                showToast(res?.message || '数据还原成功，正在重新加载页面...', 'success');
                setTimeout(() => {
                    window.location.reload();
                }, 1500);
            })
            .catch((err) => {
                console.error(err);
                showToast(`还原备份失败: ${err.message || err}`, 'error');
            })
            .finally(() => {
                setImporting(false);
                setPendingFile(null);
            });
    };

    const handleCancelImport = () => {
        setConfirmDialogOpen(false);
        setPendingFile(null);
    };

    // 触发导出会话配置
    const handleExportSessions = async () => {
        try {
            const data = await window.DisasterConfigApi.exportSessionOverrides();
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `session_configs_${new Date().toISOString().slice(0, 10)}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
            showToast('会话差异配置导出成功！', 'success');
        } catch (err) {
            console.error(err);
            showToast(`导出失败: ${err.message || err}`, 'error');
        }
    };

    // 触发导入会话配置
    const handleImportSessions = (e) => {
        const file = e.target.files[0];
        if (!file) return;
        const target = e.target;

        const reader = new FileReader();
        reader.onload = async (event) => {
            try {
                const json = JSON.parse(event.target.result);
                const res = await window.DisasterConfigApi.importSessionOverrides(json, mergeSessions);
                showToast(res?.message || '导入成功！', 'success');
                setTimeout(() => {
                    window.location.reload();
                }, 1000);
            } catch (err) {
                console.error(err);
                showToast(`解析或导入配置失败: ${err.message || err}`, 'error');
            } finally {
                target.value = '';
            }
        };
        reader.readAsText(file);
    };

    const handleCheckboxChange = (name) => (event) => {
        const checked = event.target.checked;
        setBackupTargets(prev => ({ ...prev, [name]: checked }));
    };

    return (
        <Paper elevation={0} className="config-backup-panel">
            <Box className="config-backup-title-row">
                <span>💾</span>
                <Typography variant="subtitle1" className="config-backup-title">数据备份与还原</Typography>
            </Box>
            
            <Typography variant="body2" className="config-backup-desc">
                对插件运行时产生的数据进行备份和配置迁移。
                <strong>全量备份</strong>将包含 SQLite 本地历史数据库、会话差异配置、统计快照、运行时缓存、模拟流草稿、通知缓存与日志统计。
            </Typography>

            <Divider className="config-backup-divider" />

            <Box className="config-backup-grid">
                {/* 全量/自定义备份方案 */}
                <Box className="config-backup-card">
                    <Typography variant="subtitle2" className="config-backup-card-title">数据备份 (ZIP)</Typography>
                    <Typography variant="caption" className="config-backup-card-desc" display="block">
                        支持自定义备份，适合进行异地容灾还原和服务器迁移。
                    </Typography>
                    <Box className="config-backup-btn-group">
                        <Button 
                            variant="contained" 
                            size="small" 
                            disabled={exporting || importing}
                            onClick={() => setDialogOpen(true)}
                            className="config-backup-btn-zip-export"
                        >
                            {exporting ? <CircularProgress size={16} color="inherit" style={{ marginRight: '6px' }} /> : '📤 '}
                            选择并导出备份
                        </Button>
                        <Button
                            variant="outlined"
                            size="small"
                            component="label"
                            disabled={exporting || importing}
                            className="config-backup-btn-zip-import"
                        >
                            {importing ? <CircularProgress size={16} color="inherit" style={{ marginRight: '6px' }} /> : '📥 '}
                            导入并还原
                            <input
                                type="file"
                                accept=".zip"
                                hidden
                                onChange={handleImportFullClick}
                            />
                        </Button>
                    </Box>
                </Box>

                {/* 仅配置差异备份方案 */}
                <Box className="config-backup-card">
                    <Typography variant="subtitle2" className="config-backup-card-title">仅会话差异配置 (JSON)</Typography>
                    <Typography variant="caption" className="config-backup-card-desc" display="block">
                        仅导出各群组/私聊定制的差异推送规则、白名单城市等，体积极小，导入支持增量合并。
                    </Typography>
                    <Box className="config-backup-switch-wrap">
                        <FormControlLabel
                            control={
                                <Switch 
                                    size="small" 
                                    checked={mergeSessions} 
                                    onChange={(e) => setMergeSessions(e.target.checked)} 
                                    color="primary"
                                />
                            }
                            label={<Typography variant="caption">导入时采用增量合并 (保留未冲突会话)</Typography>}
                        />
                    </Box>
                    <Box className="config-backup-btn-group">
                        <Button 
                            variant="contained" 
                            size="small" 
                            onClick={handleExportSessions}
                            className="config-backup-btn-json-export"
                        >
                            📤 导出会话配置
                        </Button>
                        <Button 
                            variant="outlined" 
                            size="small" 
                            component="label"
                            className="config-backup-btn-json-import"
                        >
                            📥 导入会话配置
                            <input 
                                type="file" 
                                accept=".json" 
                                hidden 
                                onChange={handleImportSessions} 
                            />
                        </Button>
                    </Box>
                </Box>
            </Box>

            {/* 确认导入数据弹窗 */}
            <Dialog open={confirmDialogOpen} onClose={handleCancelImport}>
                <DialogTitle style={{ fontSize: '16px', fontWeight: 700 }}>确认导入备份数据</DialogTitle>
                <DialogContent>
                    <Typography variant="body2" color="error" style={{ fontWeight: 600, marginBottom: '8px' }}>
                        警告：导入备份会根据备份包来选择性的覆盖数据内容！
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                        为了系统安全，覆盖操作前系统会在后台自动为您当前的本地数据创建 .bak 临时回滚快照。如果导入失败，数据将自动还原至当前状态。您是否要继续？
                    </Typography>
                </DialogContent>
                <DialogActions>
                    <Button onClick={handleCancelImport} size="small">取消</Button>
                    <Button onClick={handleConfirmImport} variant="contained" size="small" color="error">确认覆盖导入</Button>
                </DialogActions>
            </Dialog>

            {/* 自定义备份选择弹窗 */}
            <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)}>
                <DialogTitle style={{ fontSize: '16px', fontWeight: 700 }}>选择需要备份的数据模块</DialogTitle>
                <DialogContent>
                    <Typography variant="body2" color="text.secondary" style={{ marginBottom: '12px' }}>
                        您可以自由勾选需要导出并存入压缩包的数据文件：
                    </Typography>
                    <FormGroup>
                        <FormControlLabel
                            control={
                                <Checkbox 
                                    checked={backupTargets.db} 
                                    onChange={handleCheckboxChange('db')} 
                                    color="primary" 
                                />
                            }
                            label={<Typography variant="body2"><strong>历史预警事件库 (events.db)</strong> - 包含中国地震台网等历史记录，体积可能较大</Typography>}
                        />
                        <FormControlLabel
                            control={
                                <Checkbox 
                                    checked={backupTargets.sessions} 
                                    onChange={handleCheckboxChange('sessions')} 
                                    color="primary" 
                                />
                            }
                            label={<Typography variant="body2"><strong>会话差异配置文件 (session_overrides.json)</strong> - 包含各群组订阅等个性化设置</Typography>}
                        />
                        <FormControlLabel
                            control={
                                <Checkbox
                                    checked={backupTargets.stats}
                                    onChange={handleCheckboxChange('stats')}
                                    color="primary"
                                />
                            }
                            label={<Typography variant="body2"><strong>历史数据统计快照 (statistics.json)</strong> - 包含内存聚合的统计大屏基础数据</Typography>}
                        />
                        <FormControlLabel
                            control={
                                <Checkbox
                                    checked={backupTargets.caches}
                                    onChange={handleCheckboxChange('caches')}
                                    color="primary"
                                />
                            }
                            label={<Typography variant="body2"><strong>运行时缓存 (earthquake_lists_cache.json / eew_query_cache.json)</strong> - 包含地震列表与EEW查询状态，防止迁移后重复推送</Typography>}
                        />
                        <FormControlLabel
                            control={
                                <Checkbox
                                    checked={backupTargets.simulations}
                                    onChange={handleCheckboxChange('simulations')}
                                    color="primary"
                                />
                            }
                            label={<Typography variant="body2"><strong>模拟流草稿 (simulation_flows.json)</strong> - 包含您在模拟系统中创建的推送流程草稿</Typography>}
                        />
                        <FormControlLabel
                            control={
                                <Checkbox
                                    checked={backupTargets.notifications}
                                    onChange={handleCheckboxChange('notifications')}
                                    color="primary"
                                />
                            }
                            label={<Typography variant="body2"><strong>通知缓存 (notifications_cache.json)</strong> - 包含通知中心列表与已读状态</Typography>}
                        />
                        <FormControlLabel
                            control={
                                <Checkbox
                                    checked={backupTargets.logs}
                                    onChange={handleCheckboxChange('logs')}
                                    color="primary"
                                />
                            }
                            label={<Typography variant="body2"><strong>日志统计 (logger_stats.json)</strong> - 包含原始消息过滤统计摘要</Typography>}
                        />
                    </FormGroup>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setDialogOpen(false)} size="small">取消</Button>
                    <Button onClick={handleExportFull} variant="contained" size="small" color="primary">开始打包导出</Button>
                </DialogActions>
            </Dialog>
        </Paper>
    );
}

window.ConfigBackupPanel = ConfigBackupPanel;

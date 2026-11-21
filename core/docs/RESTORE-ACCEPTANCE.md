# 本机备份恢复验收

本流程用于验收 C 盘完整备份，不覆盖或切换生产数据库。实际故障接续另见故障恢复脚本和 NAS 计划；不要将验收目录直接当成新生产实例。

## 已通过的范围

2026-11-17 对完整备份 `20261117-014628` 完成归档 SHA-256 校验、新 ext4 镜像中的 PostgreSQL 逻辑恢复、全部 public 表计数匹配，以及用户内容和配置安全解包。随后从恢复出的数据库、内容和凭据启动隔离的同版本 Immich，现有测试照片的元数据与原图 API 校验通过，退出登录成功。

应用测试使用独立 Docker internal 网络，不发布宿主端口、不加入生产网络，不挂载生产内容，不启动 Tailscale、同步或机器学习。测试结束先停应用，再停数据库；数据库必须正常退出，最后卸载并分离 loop、只读 fsck 检查。任何非正常停库均保留现场供检查，不强制卸载。

## 复验步骤

1. 在 Meta 手动创建一次完整备份，确认成功；备份期间写入业务短暂停止。确认 C 盘有足够空间，恢复演练会创建独立数据库镜像和用户内容副本，不自动清理旧演练目录。
2. 从 `C:\nas\system\backups` 选择有 `COMPLETE` 标记的备份，运行独立恢复检查。在 WSL 仓库目录中：

```bash
sudo -n nsenter -t "$(pidof dockerd)" -m -- python3 core/scripts/restore-check.py /mnt/c/nas/system/backups/<备份目录名>
```

3. 使用上一步输出的 `restore-test-...` 目录验证恢复应用：

```bash
sudo -n nsenter -t "$(pidof dockerd)" -m -- python3 core/tests/verify-restored-app.py /mnt/c/nas/system/backups/<restore-test-目录名>
```

4. 确认 `result.json` 和 `application-result.json` 均为 PASS，并确认测试容器、网络与 loop 已清理。应用复验拒绝覆盖已有报告；需要重跑时创建新的独立恢复副本。

恢复目录包含配置、密码、私钥和相册内容，只保留在受保护的 C:\nas 下，不能上传或提交 Git。公开验收报告不含密码。

## 尚未覆盖

同一主机、相同版本、小样本恢复成功，不代表异机灾难恢复、跨架构接续、批量视频处理或恢复后的机器学习推理成功。云端/HDD 独立副本仍未接入；继续保留来源设备原文件。

隔离配置使用官方支持的 [环境变量](https://docs.immich.app/install/environment-variables/) 和 [配置文件](https://docs.immich.app/install/config-file/)，不改变生产相册设置。

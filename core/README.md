# 零号庭院 · Garden Zero NAS

项目说明与 Git 元数据位于 NAS 根目录。业务目录分成三类：`data/` 是用户内容，`core/` 是仓库根目录下维护的软件源码，`system/` 是本机配置及运行数据。用户内容保持在 D:\nas\data；本机运行状态、凭据、数据库、模型缓存和备份位于 C:\nas\system。D:\nas\system 对应子目录通过目录联接提供统一入口；SSD 历史归档与原子恢复暂存仍留在 SSD。

## 日常使用

在 `D:\nas\core` 对应 WSL 目录执行：

```bash
bash start.sh                  # 恢复保存的运行选择
bash stop.sh                   # 主动停止业务，保留管理与远程连接
bash stop.sh all               # 干净停机、卸载数据库，保留运行选择
bash scripts/host-command.sh status
bash scripts/host-command.sh backup
bash deploy.sh --check         # 只读检查
```

Windows 使用本目录 `start.ps1` / `stop.ps1`；完整停机为 `stop.ps1 -Module all`。未确认数据库已卸载前不得拔盘。不要直接重启共享 WSL 或 Docker。

## 三部分目录

- `data/immich`、`data/git`、`data/knowledge`、`data/shared`：原用户数据，路径不变。
- `core/web`、`services`、`scripts`、`tests`、`compose`、`host`、`templates`、`docs`：维护的源码与配置定义，全部进入 Git。
- `system/config`、`secrets`、`databases/immich`、`state`、`cache`、`logs`、`backups`、`installed`：C 盘运行数据的入口。
- `system/runtime`、`archive`：SSD 暂存、验证产物及历史镜像；其 `local/` 指向 C 盘对应目录。

配置模板与镜像版本锁位于 core/templates，实际配置为 C:\nas\system\config\config.json。密码位于 C:\nas\system\secrets，不提交 Git、不发送聊天。installed 是生成的部署副本，不在其中手工改源码。Linux socket、锁及数据库挂载点保留在 /run；WSL 与共享 Docker 本体不迁移。

## 界面与权限

管理台为私人 Tailscale HTTPS，未启用公网 Funnel。应用为相册、文件浏览、共享同步盘、知识库、Git 与基础服务，使用单管理员认证。路径权限递归继承，目录分工及资源统计范围见 [应用说明](docs/APPLICATIONS.md)。

更多操作见 [运维](docs/OPERATIONS.md)、[文件接入](docs/FILE-ACCESS.md)、[计划与验收记录](docs/NAS-PLAN.md)。

## 验证与发布

Python 官方入口关闭字节码缓存，测试产物写入 system/runtime，不能生成到 core。使用 `python3 -B tests/verify-layout.py` 检查目录契约；部署、认证、资源、同步、恢复等测试位于 tests。完整备份包含 data、数据库逻辑转储、core 与 system 配置/身份，历史旧备份继续保留。

项目总览与公开部署边界见 [根目录 README](../README.md)。

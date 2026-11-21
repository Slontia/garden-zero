# WSL 内 Docker Engine 安装

目标是 WSL 发行版内的原生 Engine，不依赖 Windows Docker Desktop。2026-11-15 核对 Docker 官方 Ubuntu 安装文档：支持 Ubuntu 26.04 LTS（resolute）及 amd64。

官方来源：https://docs.docker.com/engine/install/ubuntu/

## 新实例安装

在正常 WSL 终端执行：

```bash
cd /mnt/d/nas
sudo bash core/scripts/install-docker-engine.sh --install
```

此步骤需要真实宿主 root 和网络；本 AI 会话的 no-new-privileges 无法执行。脚本要求 systemd 为 PID 1，遇到已有运行时包、Docker/socket 配置或非空 Linux 运行时数据目录时拒绝继续，既不卸载，也不覆盖。已有 Docker 官方源文件也会拒绝覆盖；若之前安装中途失败，须先核查残留后恢复，不要删除文件盲目重试。

脚本添加官方签名 apt 源，按该 Ubuntu suite 的官方 stable 列表解析确切包版本，安装 Engine、CLI、containerd、Buildx、Compose 插件并启动本地 Engine；不拉取或启动 NAS 应用，不添加 docker 组成员，不搬动 Desktop 数据，不配置远程 Docker API。软件安装可能由包自带服务启动逻辑启动 Docker，脚本已在安装前拒绝发现旧 Linux 数据的环境。

安装清单和日志位于 system/runtime/engine-install-*。Engine 运行时存储保留在 Linux 默认位置，不能把 /var/lib/docker 直接迁往 NTFS。PostgreSQL 数据后续单独绑定到挂载好的 ext4 镜像目录。使用显式 /usr/bin/docker 和 unix:///var/run/docker.sock，避免 Windows CLI 或 Desktop context 混用。

脚本使用 --no-remove 防止 apt 删除依赖包；记录版本不等于 apt hold，未来系统更新可能升级包，须按运维策略复核。未改变现有防火墙；部署服务时仍须检查 Docker 端口发布与实际访问范围。

## 实测状态

**最新状态（2026-11-15）：已成功安装并运行原生 Engine 29.9.3、Compose 5.7.1、containerd 2.4.2。下文未安装描述为此前历史状态。** 安装证据：`system/runtime/engine-install-BxD7lbt7/`。Docker 与 containerd 均 active/enabled；显式本地 socket 检查通过，新引擎初始容器与卷清单为空。包版本已记录，未设置 hold。

镜像拉取目前使用 `/run/systemd/system/docker.service.d/nas-deploy-proxy.conf` 临时 HTTP(S) 代理；它不会跨宿主重启保留，尚未建立生产代理自动发现机制。终端代理与 daemon 代理分别生效。Docker 默认数据位于 WSL Linux 文件系统，新版 containerd 镜像数据亦在 `/var/lib/containerd`，二者均未迁入 NTFS。

仅通过 bash -n 语法检查和本会话 root 前置拒绝检查，未实测成功安装；不得宣称依赖已就绪。若提示 systemd 或已有状态阻断，提供错误再按现状适配，不强制移除包或初始化已有数据库。

成功后须核对日志中的 Server、Compose、DockerRootDir 和空/已有容器卷清单，再准备使用固定兼容版本 PostgreSQL 的独立 ext4 镜像测试，验证写入、正常停止、卸载及重挂后数据保留。现有 256 MiB 文件仅作为文件系统实验保留，不直接用作生产库。

首次宿主执行在下载官方签名密钥时 curl 错误 35（Connection reset by peer），尚未安装 Engine，也未写入 Docker 密钥和源。安装器现增加有限重试与超时，可以重跑原命令。如仍失败，先处理 WSL 到 download.docker.com 的 HTTPS 网络/代理，不关闭证书验证；apt 的 Ubuntu 源可达不能证明 Docker 源可达。

## 已有官方源的失败续装

2026-11-15 最新日志已出现官方密钥和源文件，但包尚未安装。此状态使用 `--resume`，它会重新下载官方密钥，并核对现有密钥与源内容，保留并拒绝不一致文件。已有运行时或数据保护仍适用。apt 索引任一源失败立即停止。此路径通过语法检查，尚待 root 实测。

当前已验证既有大写 HTTPS_PROXY 对应代理可访问官方密钥和索引；旧 set-windows-proxy.sh 的端口本轮超时。正常 WSL 终端可执行以下命令，在本机 sudo 提示中认证，不把密码交给聊天：

```bash
cd /mnt/d/nas
(
  proxy_endpoint="${HTTPS_PROXY:-${HTTP_PROXY:-}}"
  [ -n "$proxy_endpoint" ] || { echo '缺少已验证的代理配置'; exit 1; }
  export http_proxy="$proxy_endpoint" https_proxy="$proxy_endpoint"
  export HTTP_PROXY="$proxy_endpoint" HTTPS_PROXY="$proxy_endpoint"
  unset all_proxy ALL_PROXY
  sudo --preserve-env=http_proxy,https_proxy,HTTP_PROXY,HTTPS_PROXY,no_proxy,NO_PROXY bash core/scripts/install-docker-engine.sh --resume
)
```

以上只临时传递代理环境，不修改 sudoers。安装成功后再检查 Engine 和 Compose，尚不启动 NAS 业务。

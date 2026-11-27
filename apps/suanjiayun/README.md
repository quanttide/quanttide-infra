# suanjiayun —— 算家云一键 CLI

把算家云容器中心的网页操作打通成命令行：登录、查区域/节点/镜像、创建实例、等就绪、开放端口拿地址、开关机、释放。封装目标是「一条命令开一台跑 H3 的卡」，替代手动点控制台。

```bash
# 1. 凭证只进环境变量，不落仓库
export SUANJIAYUN_USERNAME=手机号
export SUANJIAYUN_PASSWORD=***

# 2. 登录（会话缓存在 ~/.config/suanjiayun/session.json，0600）
python3 apps/suanjiayun/suanjiayun.py login

# 3. 找卡、找镜像
python3 apps/suanjiayun/suanjiayun.py areas
python3 apps/suanjiayun/suanjiayun.py nodes --area 西南A区 --gpu 4090
python3 apps/suanjiayun/suanjiayun.py images --q H3

# 4. 一键：创建 → 等 running → 开端口 → 打印访问地址
python3 apps/suanjiayun/suanjiayun.py up --area 西南A区 --gpu 4090 --mirror "MiniMax-H3(综合版)一键快速使用"

# 5. 收工（关端口 + 关机停计费；--release 才是释放，不可逆）
python3 apps/suanjiayun/suanjiayun.py down <instance-id>
```

其余子命令：`whoami`（身份/项目/实例数）、`list`（实例）、`create`（创建，支持 `--dry-run` 看 payload）、`wait` / `start` / `stop` / `release` / `port`。

## 前置：首创建的人机验证码

账号**还没有任何实例**时，平台要求第一次创建过人机验证码，CLI 绕不开。先在网页上手动创建一台（用完可立即释放），之后所有创建/开关机/开端口全部走 CLI。判据是 `con_instances/my-count` 返回 0。

## 接口清单（逆向自容器中心前端，2026-11-27）

默认 API 根：`https://suanjiayun.com/container/api`；区域操作走该区域自己的 API 主机（如 `https://xn-a.suanjiayun.com:4333` + `/container/api`），区域主机从 `con_areas/list-nodes` 的 `APIUrl` 取。

| 用途 | 方法与路径 | 备注 |
|---|---|---|
| 登录 | `POST /users/login` `{username,password}` | 响应 `id`=token、`userId`；token 原样放 `Authorization` 头 |
| 身份/项目 | `GET /users/me/info`、`GET /users/{userId}/roleInfo` | 项目与 deptId 从 roleInfo 里挑（default 优先） |
| 区域与 GPU | `GET /con_areas/list-nodes?versionType&gpuType&globalOp` | 公开；versionType 0=专业 1=青春 |
| 节点与实时价 | `GET /con_nodes/realTime?filter=...` | 公开；where 必须带 areas+gpuTypes+chargeMode+count+versionType |
| 基础镜像 | `GET /con_images?filter={where:{state,type:"base",lifecycle:"ready"}}` | 公开 |
| 社区镜像 | `GET /communityMirrors`、`GET /communityMirrors/{id}` | 公开；H3 镜像在这里 |
| 实例数（验证码闸门） | `GET /con_instances/my-count` | 0 = 首创建要验证码 |
| 创建 | `POST /projects/{pid}/instances`（区域 API） | payload=buildImage/instanceTypeId/nodeId/gpuType/count/payObj/area/… |
| 列实例 | `GET /projects/{pid}/instances/own?filter=...` | loopback 风格，404 时回落 POST |
| 开机 | `POST /projects/{pid}/instances/{id}/startUp/{gpu\|nogpu}` | nogpu=无卡开机 0.01 元/时 |
| 关机 | `POST /projects/{pid}/instances/{id}/shutDown` `{id}` | |
| 释放 | `POST /projects/{pid}/instances/{id}/release` `<裸id>` | 不可逆 |
| 开/关端口 | `POST .../openPort`、`.../closePort` `{id}` | openPort 响应 `port`；访问地址 = 区域主机名:`port` |

请求头：`Authorization: <token>`（无 Bearer）、`X-Timezone: Asia/Shanghai`、`Accept-Language`。

## 已实证 / 待实证

- **已实证（无凭证即可复现）**：areas、nodes（4090 西南A区 1.98 元/时、5090 西南E区 2.92 元/时）、images 搜 H3 四个镜像、login 错误文案（401 Account or password is incorrect）、未登录 401 Authorization Required。
- **待实证（需要真账号跑一次）**：login 成功后的 roleInfo 项目解析、create payload 字段与区域 API 拼接、wait/port/up 全链路。第一次真跑建议 `create --dry-run` 先核对 payload，再实跑 `up`。

## 已知边界

- 首创建验证码：见上，人工过一次即可。
- 5090（西南E区）当前节点全满时 `nodes` 会列出但 `create` 报「没有空闲」——换区域或等释放。
- 4090 24G 跑 H3 上限 10 秒视频（见 `data/library/default/minimax-h3-local-speed.md`）。
- 实例关机 7 天自动释放；CLI 只负责关机停钱，备份走项目网盘。

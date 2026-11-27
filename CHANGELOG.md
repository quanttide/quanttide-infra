# Changelog

## [Unreleased]

- 新增算家云一键 CLI：登录、区域/节点/镜像查询、创建实例、开关机、开端口，`up`/`down` 两命令打通容器中心流程；代码在实验室仓 `quanttide-infra-lab`（`examples/quanttide-infra-lab` 入口），接口逆向自容器中心前端并实证。
- 卡价同步为专业版节点实测价：4090 ¥1.98/卡时（西南A区）、5090 ¥2.92/卡时（西南E区），首页 1.24/2.68 标注为最低档起价；library 与档案四处成本账同步。
- data/profile 清空示例骨架（IaaS/PaaS/SaaS 占位、index、CHANGELOG、Jupyter Book 配置与发布流水线），配置改用 myst.yml，新增 quanttide-founder/suanjiayun.md（算家云基础设施配置方案）。
- 新增 data/library（quanttide-library-of-infrastructure），迁入自创始人素材库的阿里云与密钥管理参考。

- 注册六个子模块（早期仓库名 `*-of-cloud-computing`，随领域规范统一改为 `*-of-infrastructure` 后入库）：`data/profile`、`docs/tutorial`、`docs/handbook`、`docs/specification`、`docs/gallery`（原 usercase）、`docs/essay`（原 industry-report）；README 由「待补充」改为数据/文档两表
- 初始化领域仓库：注册语境（`data/context` → `quanttide-context-of-infrastructure`）；journal、archive、brochure 等其余数据资产暂未创建。

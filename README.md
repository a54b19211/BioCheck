# BioCheck

潘倩维护生成质量：约束准备表达的内容，生成实际报告，核对正文与原文，最多一次有依据的修订，并单独复核所选支持片段。原文、来源版本、对象关系和机制评价由 Fabric、唐蔚雯与 MechEvidence 提供；查询与补查由李明洋的策略及雷达执行器安排。

## 启动和检查

Python 3.10+，HTTP 专业服务使用标准库。通过私有环境提供 `BIOCHECK_SERVICE_TOKEN`，服务不加载模型密钥或 `.env`，由雷达统一执行付费请求：

```sh
python3 -m app.service --host 127.0.0.1 --port 18562
python3 -m unittest discover -s tests -v
```

`GET /health`、`GET /v1/capabilities` 和 `POST /v1/execute` 均需 Bearer 令牌。请求使用 `bioradar.professional.v1`、稳定 operation_id、operation、arguments、model_receipts。服务需要模型时返回下一次公开请求；宿主保存真实调用、执行并携回回执。固定 provider_version 与原参数恢复，结果未知不盲重发。

雷达设置 `BIORADAR_BIOCHECK_TRANSPORT=http`、`BIORADAR_BIOCHECK_SERVICE_URL` 与 `BIORADAR_BIOCHECK_SERVICE_TOKEN`。完整链路及四项集成检查见 [BioRadar README](../BioRadar/README.md)；本项目本地检查为 unittest，无独立四项 Makefile。

## 用真实问题完整验证

从 Fabric 读取 DREAMM-2 等实际摘要或登记固定版本，提问“两个剂量组的 ORR、PFS、OS 和研究限制分别是什么？”雷达取证后依次运行 generate、verify、必要的 revise 与支持片段复核，保存报告及真实状态，追问另存版本。

独立 CLI 仍可使用 `python3 -m app.cli data/cases/dreamm.json --output runs/pan-first --method bound`；该文件是私有材料，缺失时先运行 `python3 -m app.prepare --output data/my-materials`，从生成的 cases.json 选择实际问题包。独立模式读取本项目私有模型配置并产生费用；雷达联调使用 HTTP 模式即可。

纯函数入口为 `app.core.run(packet, execute, method="bound")`，另有 generate/verify/revise/audit_support/quality_feedback。对象、数值、时间、条件、结论强度和引用分别检查。逐字短引有出处不等于语义支持，整篇材料支持不等于所选片段支持；正文更新后不能沿用旧审查。quality_feedback 返回缺材料、可修订或核验故障，不替查询规划器决定动作。计算结果必须带固定输入、方法、单位与计算口径，不能冒充原始研究。

## 本轮实际验证与未完成

2026-11-01 记录在 `/home/zk_test/zk_projects/.branch-archive-20261101/runs.jsonl`。系统共有 32 项业务分析、233 次新模型请求、已知 token 下界 5,863,856 和 1 次用量未知。保存的专业 HTTP 往返中 BioCheck generate 26、verify 59、quality_feedback 7 条，包含恢复往返，不等于相同数量的独立完整任务。10 份草稿中 7 份原生报告 FAIL、3 份候选未完成核验；不能将接通算成报告通过。

前后减负零调用比较涵盖全部 97 条专业往返，无结果变化。既有 read_requests 集合顺序有 3 处差异，内容一致，未为比较改写原回执。

未完成：全量 C 的 professional_rpc_operation_failed 和 2,836 字未核候选需与雷达一起恢复；长稿漏检、所选片段不支持与修订新增错误仍需独立人工标签评价。当前 PASS 是机器检查状态，不能代替独立准确率；不能用生成/核验双方共享的判断代替专业标签。

按用户最新决定，本地仅保留 main，不推送远端。旧代码保存在 BioCheck.bundle；原材料与运行日志继续保留在受控归档或原有私有数据目录中。

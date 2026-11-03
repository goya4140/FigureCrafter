# 脱离Codex运行：依赖与审查边界

审查日期：2026-11-03。审查对象是GitHub仓库当前入口，不是作者工作区中正在运行或已暂停的实验。

## 审查发现与修复

原发布版 `52d288c` 不能笼统称为完全独立：Native与Hybrid原生接续虽然自带HTTP循环，根CLI却没有开放；Image仅有手动Session工具入口；根CLI的Decomposer重建强制要求 `--codex`。历史v2.5运行区还要求私有artifact-tool与技能文档，容易与当前安装混淆。

本次只补充入口、传输适配和说明：

- `figurecrafter.py run` 开放原有Native/Hybrid HTTP循环；Image薄循环使用原Session及原providers，工具定义、科学规划、像素观察、零edit和唯一初稿门禁不变。
- `reconstruct --model-config` 使用Decomposer原有 `external-json` 后端；新增HTTP传输复用固定适配器的请求构造，传递原prompt、schema、图像和明确模型/effort。Codex CLI仅为显式可选方式。
- 运行级overlay覆盖本机工具、缓存和检测路径，显式设置refinement上限3轮；冻结的基础配置和vendor文件不改写。
- 根README和Quickstart以独立HTTP命令为主；手动代理说明改为通用模型说明。修正README误称仓库私有的文字。

没有修改19文件Native原版、四份指南、Method输入合同、科学/视觉标准、图像模型或image edit预算。没有新启动绘图、GPU检测、批量、FigureEvolver或定时任务。

## 当前各路径依赖

| 路径 | 是否需要Codex | 实际外部依赖 |
| --- | --- | --- |
| `native` | 不需要 | Python公开依赖、LibreOffice、支持图像与工具调用的HTTP模型服务 |
| `image` | 不需要 | Python公开依赖、同类HTTP推理模型、现有Nano Banana Pro任务服务与凭据；零edit |
| `hybrid` | 不需要 | 上述Image产物、SAM3/PP-OCRv5服务、Node/pptxgenjs、Poppler、LibreOffice、Decomposer结构化视觉API与原生接续HTTP模型 |
| `image-builtin` | 需要实际内置图像工具环境 | 历史可选路径；不纳入独立HTTP `run`，不假装替换成其他提供商 |
| `reconstruct --codex` | 需要 | 明确选择历史Codex CLI传输时使用 |
| 历史 `runtime/portable.py` | 含私有依赖 | v2.5保留入口，artifact-tool和技能文档要求仍存在；不作为当前安装路径 |

脱离Codex不等于无需模型API、完全本地离线，或对任意兼容服务都已验证。模板保留指定模型，API账户能否使用该模型由使用者实际验证；不自动改名或降级。

## 冻结内容与隔离的限度

`methods/decomposer/vendor/config.json`、知识种子及历史配置中仍有原机器路径；保留它们以维持原来源哈希。当前重建入口用运行级overlay替换会执行的路径，检测缓存、检测器开关等再由原 `reconstruct.prepare` 按本次输入写入。直接绕过根入口使用冻结配置，不具备同样的可移植保证。

仓库中仍出现Codex名称、历史命令及私有依赖说明，这不等于当前主路径执行时需要它们。它们也意味着不能称整个仓库所有历史入口都已去除Codex依赖。本次没有清洗Git历史或修改冻结来源。

单样例工具限制文件作用域，但模型作者Python代码仍继承宿主能力，**不是操作系统沙箱**。HTTP上下文只主动提供当前Session返回的数据；严格实验隔离仍须操作者使用容器、权限或独立环境，不能仅凭workspace目录名称声称系统级隔离。

## 已做与未做的验证

新增离线检查使用本地脚本化HTTP服务，不调用远端模型或Nano，不将fixture称为模型质量测试：

- Native与Hybrid接续：实际CLI → HTTP工具往返 → 图片字节输入 → 原Session → 作者代码 → 系统公开安装的LibreOffice渲染 → 原审查/交付/冻结 → 哈希复核。子进程PATH移除Codex与Codex运行时工具目录。
- Image：独立HTTP循环、图片传递、断点接续、重复初稿被拒绝、无自动付费提交；不伪造远端生成成功。
- Decomposer：本地HTTP往返核对原prompt、schema、图像及medium effort，模型身份不符时停止；从根CLI生成的overlay经实际 `reconstruct.prepare` 合并后核对HTTP后端、可移植路径和3轮上限。
- 既有发布合同继续核验Native19文件、各份指南、逐文件来源、799个Decomposer vendor文件、输入/写入门禁及展示文件。

未运行真实付费模型完整生成、Nano任务、SAM/OCR GPU检测或全链Decomposer重建；因此工程检查不能替代真实服务兼容性与科研绘图质量验收。没有声称改换HTTP传输后能复现历史模型质量。

运行命令及最终验证记录见 [Quickstart](QUICKSTART.zh-CN.md) 和 [验证记录](VERIFICATION.zh-CN.md)。

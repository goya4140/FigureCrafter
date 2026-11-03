# 独立HTTP入口验证 · 2026-11-03

在新建的Python 3.13虚拟环境中，仅通过公开 `requirements.txt` 安装依赖，实际渲染使用单独安装的系统LibreOffice。未安装Codex技能或artifact-tool。Native/Hybrid接续测试的子进程PATH仅含普通系统目录，不包含Codex和Codex运行时工具目录。

| 验证 | 结果 | 证据边界 |
| --- | --- | --- |
| 发布合同与新增独立入口检查 | 12项通过 | Native/Hybrid实际CLI、HTTP、图片、PPTX渲染与冻结；Image断点与唯一初稿；Decomposer HTTP及有效配置3轮 |
| 原版Native合同 | 31项通过 | 原版工具闭环、实际PPTX渲染、输入/审查/冻结门禁，HTTP使用本地fixture |
| 历史内置Image合同与预算 | 17项通过 | 原样保留累计预算和后端边界；不表示该分支已脱离内置图像环境 |
| 冻结来源 | 通过 | 原版19文件、四指南、逐文件来源与799个Decomposer vendor文件未修改 |

共60项通过，无真实远端模型或Nano生成、GPU SAM/OCR、全链Decomposer重建或新科研图实验。这些检查证明本次入口与传输的工程行为，不证明任意真实API兼容性或图像质量。[审查及仍保留的历史依赖](STANDALONE.zh-CN.md)。

复核命令沿用下文；如需运行实际渲染检查，将 `DRAWING_SOFFICE` 设置为公开安装的LibreOffice，不能留空后把跳过误报为通过。

---

## 历史分享版本验证 · 2026-10-30

验证针对可移植包装与已有真实案例，没有启动新的科研图生成、image edit、GPU识别或批量任务，也没有改动正在运行的Hybrid实验。

| 验证 | 结果 | 范围 |
| --- | --- | --- |
| 原版Native文件与四指南 | 19文件哈希一致 | 冻结v2.5.1未修改 |
| 原版合同测试 | 31项通过 | 输入隔离、工具往返、真实PPTX执行/渲染、审查与冻结；HTTP部分为本地fixture |
| 内置Image合同与预算测试 | 17项通过 | 实际输出登记、观察门禁、审查、累计3次edit与共享预算；输入fixture适配为Method |
| 分享入口测试 | 8项通过 | Method输入与写入边界、零edit及重复初稿拒绝、实际Hybrid导入/渲染、不可变文件保护、代码来源哈希与展示页链接 |
| Decomposer分享清单 | 799个vendor文件核验通过 | 排除node_modules、缓存及编译字节码；源码、知识、字体与配置保留 |
| 当前私有密钥扫描 | 未发现 | 新增代码、文档、配置模板及展示清单；不打印密钥值 |

Python环境为3.13，使用实际LibreOffice执行渲染。未新做PowerPoint/WPS现场验证；未将传输fixture当作真实模型生成效果，也未将这些工程检查解释成科学质量提升。

重跑命令：

```sh
# 仓库根；配置本机LibreOffice
export DRAWING_SOFFICE=/YOUR/soffice
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pytest methods/builtin-image/figurecrafter-v251-image/tests -q

# 冻结原版测试在对应包根运行
cd methods/drawing-v2.5.1
/ABSOLUTE/PATH/TO/.venv/bin/python -m unittest discover -s tests -v
```

Decomposer上游自身仍保留已披露的验证不足和历史测试问题。此处未声称整个vendor测试集全部通过，也未运行另一个模型比较实验。

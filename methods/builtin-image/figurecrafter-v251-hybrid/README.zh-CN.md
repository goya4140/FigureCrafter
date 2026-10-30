# v2.5.1 Hybrid 原生接续增量

原版v2.5.1源码、提示词和四份指南不修改。图像模型独立产出视觉方案，经当前固定版本Decomposer和当前图像的服务器SAM/OCR重建，然后由本目录导入不可变PPTX并回到原版execute/inspect/view/review/finish。此模块复用历史method2的机械交接/原生对象审计，不导入历史样例纠错代码。

MODEL.direct-drawing.txt保存原版完整MODEL字节，MODEL.txt在原版后附加CONTINUATION.md，唯一必要变化是要求打开已导入的可编辑PPTX并在对象上修改，而非从空白slide重画。用户认可Native文件原地保留，不能作为新图的teacher或生成输入。当前模型GPT-6.1-sol，图像阶段使用内置image_gen，累计edit不超过3，批量关闭。

当前Decomposer指针及预识别检查仍使用image-assisted-method2中的独立工具；模型后端直接使用Decomposer现有codex-cli入口，仅指定gpt-6.1-sol与实际本机运行时，不改变其科学识别/表示提示或门禁阈值。

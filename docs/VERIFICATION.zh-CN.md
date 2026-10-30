# 分享版本验证 · 2026-10-30

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

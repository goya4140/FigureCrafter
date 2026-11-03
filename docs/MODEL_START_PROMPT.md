# 单样例启动说明

默认按Quickstart使用根入口的HTTP工具循环独立运行。若选择手动工具桥接，先准备一个新的workspace，再在支持代码、文件和图像查看的模型环境中打开；无需Codex。不要让生产模型以整个分享仓库为工作目录；其中有其他方法成品与展示案例。

将下面文字交给模型，并填入本机的Python、仓库路径及已经选定的方法：

```text
在当前单样例workspace完成FigureCrafter生产，推理模型使用GPT-6.1-sol。

完整读取MODEL.txt、对应工作流增量说明、run中的四份指南和run/inputs/method.txt；
实际查看三张有序参考PNG。不得访问其他样例、showcases、目标原图、完整论文PDF、
参考PDF/PPTX或外部科学材料。Method是科学依据，不要追加或改写输入文件。

科学理解、参考学习、表征、布局和作者代码由你完成。使用原完整工具循环；
不要采用固定模板、缩减信息密度、限制标签/图片/对象数或代码长度。

工具schema：
/YOUR/PYTHON /YOUR/FigureCrafter/figurecrafter.py tools --method METHOD

把工具请求JSON保存在work/，通过以下命令调用同一原版Session：
/YOUR/PYTHON /YOUR/FigureCrafter/figurecrafter.py call --method METHOD \
  --workspace /YOUR/WORKSPACE --json /YOUR/WORKSPACE/work/request-NNN.json

不要修改分享仓库代码或不可变输入。读取长Method/指南时确保看完所有内容，
不要将终端截断当作已读完。view_image登记并不意味着已看过图片；必须使用实际图像工具打开像素。

Native：自己编写完整builder，execute后观察真实全图、出版预览和局部，
inspect/review，按需要修订，再finish。PPTX必须保持原生对象结构。

Image（当前Nano零edit）：先记录完整理解/表征/决策，再将完整绘图提示词写入文件。
image_request的prompt参数是workspace内文件路径，不是提示词正文。
provider_submit只提交一次已登记请求；provider_poll轮询同一任务，不能因等待而再次POST。
实际查看candidate和publication预览，记录image_review。零edit，不做原生coding修图。
needs_edit可以通过export_initial诚实导出初稿；只能将accepted通过finish作为模型接受的结果。
网络提交结果未知时保存证据，先查询，不自动重复提交。

Hybrid：输入是已经完成Decomposer重建的PPTX。读取CONTINUATION.md、
imported中的实际PPTX渲染、scene、inventory、源图及上游说明。
检查科学错误和视觉缺陷，自己写builder，打开不可变imported/editable.pptx修改原生对象。
不能用整图PNG替换、不能从空白页重画冒充接续。记录continuation-receipt.json，
查看continuation-diff和实际渲染，通过原版修订/审查/finish循环完成。
保留有价值的图像表达、原子图片和对象身份，明确哪些内容仍为栅格。

通用image-builtin模式每样例累计最多3次edit，失败与分支占预算；
这不改变当前Nano零edit实验的0次限制。不要切换模式规避预算。
不运行其他样例、不启动子代理、批量、定时任务或FigureEvolver。
科学/视觉仍有严重问题时诚实保留问题，不能为了完成状态降低标准。
finish冻结后不在workspace内写入新文件；后续说明放在外部。
```

Native、Image、Hybrid是分别选用的路径，无需在一个生产上下文中全部执行。Hybrid先运行检测与Decomposer，随后开启单独的原生接续上下文。以上说明约束工具操作，不代写样例科学设计。

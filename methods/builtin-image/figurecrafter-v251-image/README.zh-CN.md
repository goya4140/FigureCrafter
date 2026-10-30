# v2.5.1 Image 增量后端

`drawing-v2.5.1/` 为原版Native基线，源码及原四份指南不修改。此目录只增加image_request/result/review及位图交付的机械合同；完整原版MODEL文本作为MODEL.native-inherited.txt逐字继承，合成MODEL.txt仅在其后附加后端路由说明。科学理解、参考学习、表征、布局与质量判断均由GPT-6.1-sol执行，不增加标签/照片/对象数或代码长度限制。

图像内容只由Codex内置image_gen实际生成/编辑；代码仅校验、记录哈希、保存raw结果、对近16:9做已披露的白边填充、生成预览和位图PDF。没有外部图像API或模型降级。继承旧Image-v1.1的已测试机械门禁，保留原历史文件；新增edit_budget.py使Image与Hybrid视觉方案共用RoboScape3次edit预算，失败/分支同样计入。

本目录只定义后端，不包含特定论文提示、固定卡片/配色或模型代写布局的控制器。实际完整运行位于experiments/three-methods-v251-roboscape-gpt61sol-20261025/。Image与Hybrid独立生成，不读已认可Native成品。Hybrid视觉方案是中间检查点，可以明确携带待原生修复问题交接，不得伪造为通过的最终Image。

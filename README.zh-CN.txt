本仓库是已恢复的 drawing harness v2.5 与独立新建可移植机械适配，不包含科学数据、原图、基线、账号信息或私有运行依赖。
先读 README.rst。固定100项批量有98项可生产、048/055输入阻塞。原始完整指南存provenance/original-guides，只用于来源核验；生产由portable.py prepare载入对应production-guides。
数据单独解压production-test.zip，用runtime/import-inputs.py导入且核对392份输入SHA，再逐样本新上下文生成。新实验另建目录。评审包仅在新结果冻结后开放。

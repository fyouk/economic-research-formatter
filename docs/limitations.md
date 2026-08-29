# 当前能力边界

当前版本是 read-only Inspector/Linter v1，重点是“可确定时检查，无法确定时明确停止”。

## 不修改 DOCX

工具没有 `fix` 命令，不应用样式、不转换引用字段、不替换图片，也不重建文档。Safe Formatter 属于后续里程碑。

## 页面与渲染

Inspector 不运行 Word 布局引擎，因此不能验证真实分页、孤立标题、图表截断或字体回退。`docProps/app.xml` 的页数只标记为 `reported_page_count`，不当作实时权威页数。

## 样式解析

有效格式解析覆盖 direct run、character style、paragraph style、`basedOn` 链、docDefaults 和 theme font，并分开保存 `w:sz` 与 `w:szCs`。Word 中更复杂的条件样式、主题变体或布局结果可能仍需人工复核。

## 语义分类

Classifier 是确定性 heuristic，不使用外部 LLM API。它输出置信度与证据，但不能保证对所有非标准模板都能正确识别题名、作者、标题层级、图题或参考文献边界。低置信度或证据冲突的结果应进入 `MANUAL_REVIEW`。

## 引用与参考文献

Citation v1 只对高置信度表层模式执行检查，并排除 TOC、公式和参考文献区。它不是完整的引文解析器，也不做 Crossref/DOI 元数据核对。

外文文章标题、期刊名和出版社边界无法稳定解析时，相关规则输出 `MANUAL_REVIEW`，不伪装为已检查。

## 公式、图像与安全对象

- OMML 可确定标记为 Word Equation。MathType/OLE 仅在有关系或对象证据时报告，不根据外观猜测。
- 图像颜色分析是像素阈值指标；“大概率灰度”不等于出版流程已认证黑白图。
- 图像与 caption 的关联为相邻结构证据，不宣称语义上绝对绑定。
- Comments、tracked changes 和 embedded objects 当前主要记录主文档 OOXML 证据。
- Header/footer 关系按 section 报告，其内部段落不混入 body inventory。

## 规则范围

`unresolved.yaml` 中未规定的页边距、正文行距、正文中文字体字号等项目是 `NOT_CHECKED`，不会产生伪 `ERROR`。未裁决的 `ER-CONFLICT-001` 和 `ER-CONFLICT-002` 是 `MANUAL_REVIEW`，不会被静默自动决定。

## 分发方式

当前运行时从仓库顶层的 `rules/` 和 `sources/normalized/` 读取权威数据，因此支持的安装方式是在完整仓库 checkout 内执行 editable install。当前生成的独立 wheel 不包含仓库顶层规则资料，尚不是可脱离仓库运行的发行包。

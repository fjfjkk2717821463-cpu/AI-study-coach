on run argv
  set themeName to item 1 of argv
  set outPath to item 2 of argv
  tell application "Keynote"
    set docTheme to theme themeName
    set doc to make new document with properties {document theme:docTheme}
  tell doc
    set object text of default title item of slide 1 to "From Knowledge Anxiety to My Own AI Study Coach"
    set object text of default body item of slide 1 to "DFL Coach · Deliberate-Friction Learning — A Two-Phase Teach-First, Test-Later System"

    make new slide
    set object text of default title item of slide 2 to "费曼学习法：从哪来？"
    set object text of default body item of slide 2 to "提出者：理查德·费曼（Richard Feynman），诺贝尔物理学奖得主" & linefeed & "人称「伟大的解释者」：再难的物理，他都能讲得人人听懂" & linefeed & "方法只有四步：选概念 → 讲给外行听 → 卡壳就是没懂 → 回到原文补漏" & linefeed & "一句话：讲不清楚，就是还没真正学会"

    make new slide
    set object text of default title item of slide 3 to "一个例子：把概念讲给「奶奶」听"
    set object text of default body item of slide 3 to "概念：需求价格弹性（经济学）" & linefeed & "教科书式：需求量变动百分比 ÷ 价格变动百分比" & linefeed & "费曼式：奶茶涨 1 元，大家还买吗？少买很多＝「很有弹性」，照买不误＝「缺乏弹性」" & linefeed & "讲不出生活化的例子 = 这里存在理解漏洞"

    make new slide
    set object text of default title item of slide 4 to "实践的难题，AI 恰好能补上"
    set object text of default body item of slide 4 to "难题一：缺少一个耐心、随时在线的听众" & linefeed & "难题二：人容易自我感觉良好，跳过自己的漏洞" & linefeed & "难题三：复述没人纠错，也无法逐句对照原文" & linefeed & "AI 的优势：无限耐心、没有面子压力、能抛反例、能逐段核对原文" & linefeed & "费曼法给出方法，DFL Coach 当那个永远在线的陪练"

    make new slide
    set object text of default title item of slide 5 to "问题：为什么读懂了，却不会用？"
    set object text of default body item of slide 5 to "学习错觉：看一遍、划重点不等于掌握" & linefeed & "费曼技巧：讲给别人听，暴露理解漏洞" & linefeed & "最大的痛点：缺少一个耐心、随时可用的倾听者" & linefeed & "AI 能扮演这个角色，但直接给答案会让人失去思考"

    make new slide
    set object text of default title item of slide 6 to "核心思路：让 AI 当教练，而不是答案机"
    set object text of default body item of slide 6 to "教练手里有答案，职责是「先教、再考」" & linefeed & "第一阶段：把概念准确、通俗地讲清楚" & linefeed & "第二阶段：用费曼式输出检验并加深" & linefeed & "一句话：提问必须建立在讲解之后"

    make new slide
    set object text of default title item of slide 7 to "学习流程：两个阶段"
    set object text of default body item of slide 7 to "阶段一 · 概念精讲：保证准确输入" & linefeed & "阶段二 · 费曼检测：通过输出加深" & linefeed & "学习者掌握节奏，确认理解后再进入检测"

    make new slide
    set object text of default title item of slide 8 to "阶段一：概念精讲"
    set object text of default body item of slide 8 to "知识地图：概念清单和逻辑主线" & linefeed & "每个概念：定义 → 直觉 → 2-3 个例子" & linefeed & "再补充：反例或边界、常见误区、原文出处" & linefeed & "每个概念结尾只做轻量确认，不考试"

    make new slide
    set object text of default title item of slide 9 to "阶段二：费曼检测"
    set object text of default body item of slide 9 to "复述：合上材料用大白话讲" & linefeed & "反例与边界：什么情况下不成立" & linefeed & "逻辑显形：画出推理链" & linefeed & "多维对撞：考官追问、玩偶讲解、录音复盘" & linefeed & "默写比对：凭记忆重构，与原文逐段对照"

    make new slide
    set object text of default title item of slide 10 to "产品功能一览"
    set object text of default body item of slide 10 to "导入：txt / md / pdf / epub / 网页链接" & linefeed & "文档自动转 Markdown：保留标题、列表、表格" & linefeed & "章节层级：大章节 / 小章节 / 自动" & linefeed & "讲解强度：多讲解 / 适中 / 少讲解" & linefeed & "流式输出、结构化总结、复习中心、继续学习"

    make new slide
    set object text of default title item of slide 11 to "最新优化：更安全、更稳定"
    set object text of default body item of slide 11 to "安全：远程访问加密码，API Key 不再被白嫖" & linefeed & "安全：路径校验 + 网页导入拦截，堵住文件读取漏洞" & linefeed & "安全：AI 输出消毒，防止恶意内容注入" & linefeed & "稳定：每条消息自动保存，崩溃不丢进度；流式回复自动重试" & linefeed & "工程：50MB 文件上限、编码检测、依赖锁版本、15 个自动化测试"

    make new slide
    set object text of default title item of slide 12 to "现场演示"
    set object text of default body item of slide 12 to "导入一本书，选择章节" & linefeed & "回复「开始」→ 阶段一概念精讲" & linefeed & "回复「开始检测」→ 阶段二费曼检测" & linefeed & "生成总结 → 复习中心查看"

    make new slide
    set object text of default title item of slide 13 to "它是怎么运行的？"
    set object text of default body item of slide 13 to "三部分接力合作：" & linefeed & "① 界面：桌面窗口 / 网页 / 手机，负责看和打字" & linefeed & "② 本地服务：读文档、切章节、管记忆" & linefeed & "③ DeepSeek 大脑：真正讲解和出题的部分" & linefeed & "书和网页先转成干净的文字版，再交给 AI" & linefeed & "回答逐字显示；对话和总结都自动保存"

    make new slide
    set object text of default title item of slide 14 to "为什么回复这么快？"
    set object text of default body item of slide 14 to "流式输出：第一个字一秒内出现，边生成边显示，不用等整段写完" & linefeed & "上下文精简：只带当前章节（最多 1.2 万字），不塞整本书" & linefeed & "输出克制：提示词要求一次只问一个问题，回复更短更聚焦" & linefeed & "DeepSeek 生成快；本地服务 + Markdown 渲染几乎零开销" & linefeed & "快的是「体感速度」：首字即达 + 短回复，而不是牺牲讲解质量"

    make new slide
    set object text of default title item of slide 15 to "开发历程：从小脚本到完整 App"
    set object text of default body item of slide 15 to "① 命令行版：先验证这套方法真的管用" & linefeed & "② 加保险：保护密钥、出错重试、逐字回复" & linefeed & "③ 做界面：网页版 + 书架 + 复习中心" & linefeed & "④ 能分发：打包成 App，手机也能访问" & linefeed & "⑤ 磨内容：epub、网页导入、两阶段流程" & linefeed & "⑥ 开门迎客：整理干净、开源分享"

    make new slide
    set object text of default title item of slide 16 to "三条开发经验"
    set object text of default body item of slide 16 to "先验证方法，再美化界面" & linefeed & "提示词就是产品逻辑" & linefeed & "按优先级迭代：健壮性 → 体验 → 分发"

    make new slide
    set object text of default title item of slide 17 to "设计与难点"
    set object text of default body item of slide 17 to "最初「只问不讲」→ 重构为「先讲后测」" & linefeed & "上下文管理：长章节截断，未来可做向量检索" & linefeed & "章节切分：不同书格式差异，epub 用原书目录"

    make new slide
    set object text of default title item of slide 18 to "总结与展望"
    set object text of default body item of slide 18 to "把一种学习方法固化成可复用系统" & linefeed & "现状：macOS / Windows / 手机 / 云端" & linefeed & "未来：向量检索、云端持久化、多用户、移动签名"

    make new slide
    set object text of default title item of slide 19 to "Thank You & Discussion"
    set object text of default body item of slide 19 to "GitHub：https://github.com/fjfjkk2717821463-cpu/AI-study-coach" & linefeed & "项目名：DFL Coach（Deliberate Friction Learning）" & linefeed & "欢迎交流、试用和提出建议"

    save in (POSIX file outPath)
    close
  end tell
end tell
return "完成"
end run

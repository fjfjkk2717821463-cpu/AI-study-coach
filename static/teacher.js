(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const state = {
    classes: [],
    classId: "",
    stats: null,
    scope: { book_name: "", chapters: [] },
    advice: null,
    meta: {},
    adopted: new Set(),
    books: [],
  };

  function esc(text) {
    const div = document.createElement("div");
    div.textContent = text == null ? "" : String(text);
    return div.innerHTML;
  }

  async function api(url, options) {
    const resp = await fetch(url, options);
    let data = {};
    try {
      data = await resp.json();
    } catch (err) {
      data = {};
    }
    if (!resp.ok) {
      throw new Error(data.error || "请求失败（" + resp.status + "）");
    }
    return data;
  }

  function setError(message) {
    $("classError").textContent = message || "";
  }

  // ------------------------------------------------------------ 主题

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    $("themeBtn").textContent = theme === "dark" ? "浅色" : "深色";
    localStorage.setItem("dfc-theme", theme);
  }

  $("themeBtn").addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme");
    applyTheme(current === "dark" ? "light" : "dark");
  });

  // ------------------------------------------------------------ 班级

  async function loadClasses() {
    const data = await api("/api/teacher/classes");
    state.classes = data.classes || [];
    const select = $("classSelect");
    select.innerHTML = "";

    if (!state.classes.length) {
      $("emptyState").classList.remove("hidden");
      $("metricsSection").classList.add("hidden");
      $("listsSection").classList.add("hidden");
      $("taskPanel").classList.add("hidden");
      $("adviceSection").classList.add("hidden");
      $("classHint").textContent = "";
      state.classId = "";
      return;
    }

    $("emptyState").classList.add("hidden");
    $("metricsSection").classList.remove("hidden");
    $("listsSection").classList.remove("hidden");
    $("taskPanel").classList.remove("hidden");
    $("adviceSection").classList.remove("hidden");

    state.classes.forEach((item) => {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.name + "（" + item.code + "）";
      select.appendChild(option);
    });
    if (!state.classId || !state.classes.some((item) => item.id === state.classId)) {
      state.classId = state.classes[0].id;
    }
    select.value = state.classId;
  }

  function currentClass() {
    return state.classes.find((item) => item.id === state.classId) || null;
  }

  $("classSelect").addEventListener("change", async () => {
    state.classId = $("classSelect").value;
    state.scope = { book_name: "", chapters: [] };
    state.advice = null;
    state.adopted = new Set();
    $("adviceList").innerHTML = "";
    $("adviceMeta").textContent = "";
    await loadOverview();
  });

  $("createClassBtn").addEventListener("click", async () => {
    const name = $("newClassName").value.trim();
    if (!name) {
      setError("请先填写班级名称。");
      return;
    }
    setError("");
    $("createClassBtn").disabled = true;
    try {
      const data = await api("/api/teacher/classes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      $("newClassName").value = "";
      state.classId = data.class.id;
      await loadClasses();
      await loadOverview();
      $("classHint").textContent =
        "已创建「" + data.class.name + "」，班级码 " + data.class.code + "，发给学生即可加入。";
    } catch (err) {
      setError(err.message);
    } finally {
      $("createClassBtn").disabled = false;
    }
  });

  $("refreshBtn").addEventListener("click", () => loadOverview(true));

  function scopeQuery() {
    const params = new URLSearchParams();
    params.set("class_id", state.classId);
    if (state.scope.book_name) params.set("book_name", state.scope.book_name);
    if (state.scope.chapters.length) params.set("chapters", state.scope.chapters.join(","));
    return params.toString();
  }

  // ------------------------------------------------------------ 数据总览

  async function loadOverview(keepAdvice) {
    if (!state.classId) return;
    setError("");
    try {
      const data = await api("/api/teacher/overview?" + scopeQuery());
      state.stats = data.stats;
      renderClassHint();
      renderMetrics();
      renderWeakList();
      renderTasks();
      renderAssignList();
      renderScopeSelect();
      if (!keepAdvice) {
        $("adviceList").innerHTML = "";
        $("adviceMeta").textContent = "";
        state.advice = null;
      }
    } catch (err) {
      setError(err.message);
    }
  }

  function renderClassHint() {
    const item = currentClass();
    const stats = state.stats || {};
    if (!item) return;
    const scopeText = state.scope.book_name
      ? "当前范围：" + state.scope.book_name +
        (state.scope.chapters.length ? " · " + state.scope.chapters.join("、") : "")
      : "当前范围：全部学习数据";
    $("classHint").textContent =
      "班级：" + item.name + " · 班级码 " + item.code +
      " · 学生 " + (stats.students_total || 0) + " 人" +
      " · 已上报 " + (stats.students_reported || 0) + " 人 · " + scopeText;
  }

  function renderMetrics() {
    const stats = state.stats || {};
    const effective = stats.avg_minutes_effective || 0;
    const cards = [
      { name: "班级人数", num: stats.students_total || 0 },
      { name: "已上报学情", num: stats.students_reported || 0 },
      { name: "达标率", num: (stats.mastery_rate || 0) + "%" },
      {
        name: effective ? "人均有效学习" : "人均会话时长",
        num: (effective || stats.avg_minutes || 0) + " 分钟",
      },
    ];
    $("metrics").innerHTML = cards
      .map(
        (card) =>
          '<div class="metric"><div class="num">' + esc(card.num) +
          '</div><div class="name">' + esc(card.name) + "</div></div>"
      )
      .join("");

    if (!stats.students_reported) {
      $("metricsHint").textContent =
        "还没有学生上报学习摘要。学生完成一章并点「生成总结」后，数据会自动同步过来。";
      return;
    }
    const parts = [
      "达标口径：" + (stats.mastery_rule || "复述质量、默写覆盖、复习重测三项过两项"),
      "已达标 " + (stats.students_mastered || 0) + " 人",
      "人均学习轮次 " + (stats.avg_rounds || 0) + " 轮",
      "数据更新时间：" + (stats.generated_at || "刚刚"),
    ];
    const chapterStats = (stats.chapter_stats || []).filter((item) => item.retell_scored);
    if (chapterStats.length) {
      parts.splice(
        1,
        0,
        "复述均分 " + chapterStats[0].retell_avg + " / 12（" + chapterStats[0].retell_scored + " 人已评）"
      );
    }
    $("metricsHint").textContent = parts.join("；") + "。";
  }

  function renderWeakList() {
    const box = $("weakList");
    const items = (state.stats && state.stats.weak_concepts) || [];
    if (!items.length) {
      box.innerHTML = '<p class="hint">还没有待巩固的概念。</p>';
      return;
    }
    const max = Math.max.apply(null, items.map((item) => item.count || 1));
    box.innerHTML = items
      .map((item) => {
        const width = Math.max(6, Math.round(((item.count || 0) / max) * 100));
        const evidence = (item.evidence || [])
          .map(
            (ev) =>
              "<blockquote>" + esc(ev.display_name || ev.student_id) + "：" +
              esc(ev.quote) + "</blockquote>"
          )
          .join("");
        const detail = evidence
          ? '<details><summary>学生原话（' + (item.evidence || []).length + " 条）</summary>" +
            evidence + "</details>"
          : '<p class="hint">暂无学生原话证据（依据：概念频次）</p>';
        const chapters = (item.chapters || []).length
          ? '<span class="pill">' + esc((item.chapters || []).join("、")) + "</span>"
          : "";
        return (
          '<div class="weak-item">' +
          '<div class="weak-head"><span class="weak-term">' + esc(item.term) + "</span>" +
          '<span class="weak-count">' + (item.count || 0) + " 人卡住 " + chapters + "</span></div>" +
          '<div class="bar"><span style="width:' + width + '%"></span></div>' +
          detail +
          "</div>"
        );
      })
      .join("");
  }

  function renderTasks() {
    const box = $("taskList");
    const items = (state.stats && state.stats.assignments) || [];
    if (!items.length) {
      box.innerHTML =
        '<p class="hint">还没有布置任务。学生仍可自主学习，数据同样会汇总到这里。</p>';
      return;
    }
    box.innerHTML = items
      .map((item) => {
        const total = (item.completed_count || 0) + (item.pending_count || 0);
        const unmastered = item.unmastered_students || [];
        const pendingNames = unmastered
          .slice(0, 8)
          .map((student) => student.display_name)
          .join("、");
        const more = unmastered.length > 8 ? " 等 " + unmastered.length + " 人" : "";
        const pending = unmastered.length
          ? '<p class="hint">还没达标：' + esc(pendingNames) + esc(more) + "</p>"
          : '<p class="hint ok">全员达标</p>';
        return (
          '<div class="task">' +
          "<div><strong>" + esc(item.book_name || "未指定书目") + "</strong> · " +
          esc((item.chapters || []).join("、")) + "</div>" +
          '<div class="hint">已学习 ' + (item.completed_count || 0) + " / " + total +
          " 人 · 已达标 " + (item.mastered_count || 0) + " / " + total + " 人" +
          (item.due_at ? " · 截止 " + esc(item.due_at) : "") + "</div>" +
          pending +
          "</div>"
        );
      })
      .join("");
  }

  // ------------------------------------------------------------ 布置任务

  async function loadBooks() {
    try {
      const resp = await fetch("/api/shelf");
      state.books = await resp.json();
    } catch (err) {
      state.books = [];
    }
    const select = $("assignBook");
    select.innerHTML = "";
    (state.books || []).forEach((book) => {
      const option = document.createElement("option");
      option.value = book.path;
      option.textContent = book.name;
      option.dataset.name = book.name;
      select.appendChild(option);
    });
    if ((state.books || []).length) await loadChapters();
  }

  async function loadChapters() {
    const path = $("assignBook").value;
    const select = $("assignChapter");
    select.innerHTML = "";
    if (!path) return;
    try {
      const data = await api("/api/books/chapters", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, level: "auto" }),
      });
      (data.chapters || []).forEach((chapter) => {
        const option = document.createElement("option");
        option.value = chapter.title;
        option.textContent = chapter.title;
        select.appendChild(option);
      });
    } catch (err) {
      $("assignError").className = "error";
      $("assignError").textContent = "读取章节失败：" + err.message;
    }
  }

  $("assignBook").addEventListener("change", loadChapters);

  $("addAssignBtn").addEventListener("click", async () => {
    if (!state.classId) return;
    const bookOption = $("assignBook").selectedOptions[0];
    const bookName = bookOption ? bookOption.dataset.name || bookOption.textContent : "";
    const chapters = Array.from($("assignChapter").selectedOptions).map(
      (option) => option.value
    );
    if (!bookName || !chapters.length) {
      $("assignError").className = "error";
      $("assignError").textContent = "请先选择教材，并至少选中一个章节。";
      return;
    }
    $("addAssignBtn").disabled = true;
    $("assignError").textContent = "";
    try {
      await api("/api/teacher/assignments", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          class_id: state.classId,
          book_name: bookName,
          chapters,
          due_at: $("assignDue").value,
          requirement: $("assignRequirement").value.trim(),
        }),
      });
      $("assignRequirement").value = "";
      await loadOverview(true);
      $("assignError").className = "hint";
      $("assignError").textContent =
        "已发布：" + bookName + " · " + chapters.join("、") + "，学生端可以看到。";
    } catch (err) {
      $("assignError").className = "error";
      $("assignError").textContent = err.message;
    } finally {
      $("addAssignBtn").disabled = false;
    }
  });

  function renderAssignList() {
    const box = $("assignList");
    const items = (state.stats && state.stats.assignments) || [];
    if (!items.length) {
      box.innerHTML = '<p class="hint">还没有布置任务。</p>';
      return;
    }
    box.innerHTML = items
      .map((item) => {
        const total = (item.completed_count || 0) + (item.pending_count || 0);
        return (
          '<div class="task">' +
          "<div><strong>" + esc(item.book_name || "未指定书目") + "</strong> · " +
          esc((item.chapters || []).join("、")) + "</div>" +
          '<div class="hint">已达标 ' + (item.mastered_count || 0) + " / " + total +
          " 人" + (item.due_at ? " · 截止 " + esc(item.due_at) : "") +
          (item.requirement ? " · 要求：" + esc(item.requirement) : "") +
          "</div></div>"
        );
      })
      .join("");
  }

  function renderScopeSelect() {
    const select = $("scopeSelect");
    const items = (state.stats && state.stats.assignments) || [];
    const previous = select.value;
    select.innerHTML = '<option value="">统计范围：全部学习数据</option>';
    items.forEach((item, index) => {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent =
        "任务：" + (item.book_name || "未指定书目") + " · " +
        (item.chapters || []).join("、");
      select.appendChild(option);
    });
    select.value = previous && items[Number(previous)] ? previous : "";
  }

  $("scopeSelect").addEventListener("change", async () => {
    const value = $("scopeSelect").value;
    const items = (state.stats && state.stats.assignments) || [];
    if (value === "" || !items[Number(value)]) {
      state.scope = { book_name: "", chapters: [] };
    } else {
      const item = items[Number(value)];
      state.scope = {
        book_name: item.book_name || "",
        chapters: (item.chapters || []).slice(),
      };
    }
    await loadOverview();
  });

  // ------------------------------------------------------------ 教学建议

  async function generateAdvice(refresh) {
    if (!state.classId) return;
    const button = refresh ? $("regenAdviceBtn") : $("genAdviceBtn");
    button.disabled = true;
    $("adviceError").textContent = "";
    $("adviceMeta").textContent = "正在生成本次教学建议……";
    try {
      const data = await api("/api/teacher/advice", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          class_id: state.classId,
          book_name: state.scope.book_name,
          chapters: state.scope.chapters,
          refresh: !!refresh,
        }),
      });
      state.stats = data.stats || state.stats;
      state.advice = data.advice || null;
      state.meta = data.meta || {};
      state.adopted = new Set();
      renderClassHint();
      renderMetrics();
      renderWeakList();
      renderTasks();
      renderAdvice(data);
    } catch (err) {
      $("adviceError").textContent = err.message;
      $("adviceMeta").textContent = "";
    } finally {
      button.disabled = false;
    }
  }

  $("genAdviceBtn").addEventListener("click", () => generateAdvice(false));
  $("regenAdviceBtn").addEventListener("click", () => generateAdvice(true));

  function renderAdvice(payload) {
    const advice = payload.advice || {};
    const meta = payload.meta || {};
    const box = $("adviceList");
    box.innerHTML = "";

    if (payload.message) {
      const banner = document.createElement("div");
      banner.className = "banner";
      banner.textContent = payload.message;
      box.appendChild(banner);
    } else if (advice.degraded) {
      const banner = document.createElement("div");
      banner.className = "banner";
      banner.textContent =
        "本次只有统计与证据：模型建议未能通过校验，已自动降级（" +
        (advice.degraded_reason || "模型不可用") + "）。";
      box.appendChild(banner);
    }

    const based = advice.based_on || {};
    const parts = ["依据 " + (based.students_reported || 0) + " 名学生上报的学习摘要"];
    if (payload.cached) parts.push("结果来自缓存");
    parts.push(meta.has_chapter_text ? "已结合教材原文" : "未取到教材原文，仅依据学情数据");
    if (meta.issues && meta.issues.length) parts.push("校验修正 " + meta.issues.length + " 处");
    $("adviceMeta").textContent = parts.join(" · ");

    const toolbar = document.createElement("div");
    toolbar.className = "row";
    toolbar.style.marginTop = "10px";
    const copyAll = document.createElement("button");
    copyAll.type = "button";
    copyAll.textContent = "复制全部（Markdown）";
    copyAll.addEventListener("click", () => {
      copyText(adviceToMarkdown());
      copyAll.textContent = "已复制";
      setTimeout(() => {
        copyAll.textContent = "复制全部（Markdown）";
      }, 2000);
    });
    toolbar.appendChild(copyAll);
    const adoptedInfo = document.createElement("span");
    adoptedInfo.className = "hint";
    adoptedInfo.id = "adoptedInfo";
    toolbar.appendChild(adoptedInfo);
    box.appendChild(toolbar);

    (advice.concepts || []).forEach((concept) => {
      box.appendChild(renderAdviceCard(concept));
    });
    updateAdoptedInfo();

    if (!(advice.concepts || []).length) {
      const hint = document.createElement("p");
      hint.className = "hint";
      hint.textContent = "还没有可讲的薄弱概念，先让学生完成学习任务。";
      box.appendChild(hint);
    }
  }

  function renderAdviceCard(concept) {
    const card = document.createElement("div");
    card.className = "advice-card";

    const head = document.createElement("div");
    head.className = "advice-head";
    head.innerHTML =
      "<strong>" + esc(concept.name) + "</strong>" +
      '<span class="pill">' + (concept.struggling_count || 0) + " 人卡住</span>" +
      '<span class="pill">依据：' + esc(concept.evidence_basis || "概念频次") + "</span>" +
      ((concept.chapters || []).length
        ? '<span class="pill">' + esc(concept.chapters.join("、")) + "</span>"
        : "");
    card.appendChild(head);

    [
      ["典型误区", "misconception"],
      ["讲解切入", "explain_action"],
      ["课堂活动", "activity"],
      ["当堂追问", "check_question"],
    ].forEach(([label, field]) => {
      const row = document.createElement("div");
      row.className = "advice-row";
      const key = document.createElement("span");
      key.className = "k";
      key.textContent = label;
      const val = document.createElement("span");
      val.className = "v";
      val.dataset.field = field;
      val.textContent = concept[field] || "（本次未生成，可先按统计讲评）";
      row.appendChild(key);
      row.appendChild(val);
      card.appendChild(row);
    });

    const evidence = concept.evidence || [];
    if (evidence.length) {
      const details = document.createElement("details");
      details.innerHTML =
        "<summary>学生原话（" + evidence.length + " 条）</summary>" +
        evidence.map((quote) => "<blockquote>" + esc(quote) + "</blockquote>").join("");
      card.appendChild(details);
    }

    const actions = document.createElement("div");
    actions.className = "advice-actions";

    const adoptBtn = document.createElement("button");
    adoptBtn.type = "button";
    adoptBtn.textContent = "采纳";
    adoptBtn.addEventListener("click", () => {
      if (state.adopted.has(concept.name)) {
        state.adopted.delete(concept.name);
        adoptBtn.textContent = "采纳";
      } else {
        state.adopted.add(concept.name);
        adoptBtn.textContent = "已采纳";
      }
      updateAdoptedInfo();
    });

    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.textContent = "编辑";
    let editing = false;
    editBtn.addEventListener("click", () => {
      editing = !editing;
      card.querySelectorAll(".advice-row .v").forEach((node) => {
        node.contentEditable = editing ? "true" : "false";
        node.style.outline = editing ? "1px dashed var(--border)" : "none";
        node.style.padding = editing ? "4px 6px" : "0";
      });
      if (editing) {
        editBtn.textContent = "保存修改";
      } else {
        card.querySelectorAll(".advice-row .v").forEach((node) => {
          concept[node.dataset.field] = node.textContent.trim();
        });
        editBtn.textContent = "编辑";
      }
    });

    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.textContent = "复制";
    copyBtn.addEventListener("click", () => {
      copyText(conceptToMarkdown(concept));
      copyBtn.textContent = "已复制";
      setTimeout(() => {
        copyBtn.textContent = "复制";
      }, 2000);
    });

    actions.appendChild(adoptBtn);
    actions.appendChild(editBtn);
    actions.appendChild(copyBtn);
    card.appendChild(actions);
    return card;
  }

  function updateAdoptedInfo() {
    const info = $("adoptedInfo");
    if (!info) return;
    info.textContent = state.adopted.size
      ? "已采纳 " + state.adopted.size + " 条，可复制进教案或教研记录。"
      : "";
  }

  function conceptToMarkdown(concept) {
    const lines = [];
    lines.push(
      "### " + (concept.name || "") + "（" + (concept.struggling_count || 0) + " 人卡住）"
    );
    if (concept.misconception) lines.push("- 典型误区：" + concept.misconception);
    (concept.evidence || []).forEach((quote) => lines.push('  - 学生原话："' + quote + '"'));
    if (concept.explain_action) lines.push("- 讲解切入：" + concept.explain_action);
    if (concept.activity) lines.push("- 课堂活动：" + concept.activity);
    if (concept.check_question) lines.push("- 当堂追问：" + concept.check_question);
    return lines.join("\n");
  }

  function adviceToMarkdown() {
    const advice = state.advice || {};
    const item = currentClass();
    const based = advice.based_on || {};
    const lines = [];
    lines.push("# 教学建议 · " + (item ? item.name : ""));
    if (advice.chapter) lines.push("章节：" + advice.chapter);
    lines.push(
      "依据：" + (based.students_reported || 0) + " 名学生上报的学习摘要" +
        (based.generated_at ? " · " + based.generated_at : "")
    );
    lines.push("");
    (advice.concepts || []).forEach((concept) => {
      lines.push(conceptToMarkdown(concept));
      lines.push("");
    });
    if ((advice.class_actions || []).length) {
      lines.push("## 班级提醒");
      advice.class_actions.forEach((action) => lines.push("- " + action));
      lines.push("");
    }
    if (advice.caveat) lines.push("> " + advice.caveat);
    return lines.join("\n");
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).catch(() => fallbackCopy(text));
      return;
    }
    fallbackCopy(text);
  }

  function fallbackCopy(text) {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try {
      document.execCommand("copy");
    } catch (err) {
      window.prompt("复制下面的内容：", text);
    }
    document.body.removeChild(area);
  }

  // ------------------------------------------------------------ 启动

  async function boot() {
    applyTheme(document.documentElement.getAttribute("data-theme") || "light");
    try {
      await loadClasses();
      if (state.classId) await loadOverview();
      await loadBooks();
    } catch (err) {
      setError(err.message);
    }
  }

  boot();
})();

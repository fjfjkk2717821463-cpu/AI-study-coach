    let mode = "book";
    let sessionId = null;
    let allChapterTitles = [];
    let lastAssistantWrap = null;
    let presetsCache = {};
    let currentAppSettings = {};

    const $ = (id) => document.getElementById(id);

    function escapeHtml(text) {
      const div = document.createElement("div");
      div.textContent = text == null ? "" : String(text);
      return div.innerHTML;
    }

    function showSection(name) {
      ["setup", "chatSection", "bookshelfSection", "historySection", "reviewSection", "conceptMapSection"].forEach((id) => {
        $(id).classList.toggle("hidden", id !== name);
      });
      closeChapterMenu();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    $("historyBtn").addEventListener("click", async () => {
      showSection("historySection");
      $("historyError").textContent = "";
      await loadSessions();
    });

    $("reviewBtn").addEventListener("click", async () => {
      showSection("reviewSection");
      await Promise.all([loadReview(), loadDueReviews()]);
    });

    $("conceptMapBtn").addEventListener("click", async () => {
      showSection("conceptMapSection");
      await loadConceptMap();
    });

    $("shelfBtn").addEventListener("click", async () => {
      showSection("bookshelfSection");
      $("shelfError").textContent = "";
      await loadShelfList();
    });

    $("historyBackBtn").addEventListener("click", () => showSection("setup"));
    $("reviewBackBtn").addEventListener("click", () => showSection("setup"));
    $("conceptMapBackBtn").addEventListener("click", () => showSection("setup"));
    $("bookshelfBackBtn").addEventListener("click", () => showSection("setup"));

    function switchMode(nextMode) {
      mode = nextMode;
      $("bookTab").classList.toggle("active", mode === "book");
      $("outlineTab").classList.toggle("active", mode === "outline");
      $("bookSetup").classList.toggle("hidden", mode !== "book");
      $("outlineSetup").classList.toggle("hidden", mode !== "outline");
      $("setupError").textContent = "";
    }

    $("bookTab").addEventListener("click", () => switchMode("book"));
    $("outlineTab").addEventListener("click", () => switchMode("outline"));

    async function loadShelf() {
      try {
        const resp = await fetch("/api/shelf");
        const books = await resp.json();
        const select = $("bookSelect");
        select.innerHTML = "";
        if (!books.length) {
          const opt = document.createElement("option");
          opt.textContent = "书架为空，请先添加书籍";
          opt.disabled = true;
          select.appendChild(opt);
          return;
        }
        books.forEach((book) => {
          const opt = document.createElement("option");
          opt.value = JSON.stringify({ name: book.name, path: book.path });
          opt.textContent = book.name;
          select.appendChild(opt);
        });
        loadChapters(JSON.parse(select.value).path);
      } catch (err) {
        $("setupError").textContent = "读取书架失败：" + err.message;
      }
    }

    async function loadShelfList() {
      const list = $("shelfList");
      list.innerHTML = "";
      try {
        const resp = await fetch("/api/shelf");
        const books = await resp.json();
        if (!books.length) {
          list.innerHTML = '<p class="empty-hint">书架还是空的，请在下方导入书籍。</p>';
          return;
        }
        books.forEach((book) => {
          const row = document.createElement("div");
          row.className = "shelf-row";
          const info = document.createElement("div");
          info.innerHTML =
            '<div class="shelf-name"></div><div class="shelf-path"></div>';
          info.querySelector(".shelf-name").textContent = book.name;
          info.querySelector(".shelf-path").textContent = book.path;
          const remove = document.createElement("button");
          remove.type = "button";
          remove.textContent = "移除";
          remove.addEventListener("click", () => removeBook(book.path));
          row.appendChild(info);
          row.appendChild(remove);
          list.appendChild(row);
        });
      } catch (err) {
        $("shelfError").textContent = "读取书架失败：" + err.message;
      }
    }

    async function addBook() {
      const fileInput = $("bookFileInput");
      const nameInput = $("bookNameInput");
      if (!fileInput.files.length) {
        $("shelfError").textContent = "请先选择要导入的文件。";
        return;
      }
      const formData = new FormData();
      formData.append("file", fileInput.files[0]);
      formData.append("name", nameInput.value.trim());
      $("addBookBtn").disabled = true;
      $("shelfError").textContent = "正在导入，请稍候……";
      try {
        const resp = await fetch("/api/shelf", {
          method: "POST",
          body: formData,
        });
        const data = await resp.json();
        if (!resp.ok) {
          $("shelfError").textContent = data.error || "导入失败。";
          return;
        }
        $("shelfError").textContent = "";
        fileInput.value = "";
        nameInput.value = "";
        await Promise.all([loadShelfList(), loadShelf()]);
      } catch (err) {
        $("shelfError").textContent = "导入失败：" + err.message;
      } finally {
        $("addBookBtn").disabled = false;
      }
    }

    async function removeBook(path) {
      try {
        const resp = await fetch("/api/shelf/remove", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          $("shelfError").textContent = data.error || "移除失败。";
          return;
        }
        await Promise.all([loadShelfList(), loadShelf()]);
      } catch (err) {
        $("shelfError").textContent = "移除失败：" + err.message;
      }
    }

    $("addBookBtn").addEventListener("click", addBook);

    async function importWebPage() {
      const url = $("webUrlInput").value.trim();
      if (!url) {
        $("shelfError").textContent = "请输入要导入的网页地址。";
        return;
      }
      $("importWebBtn").disabled = true;
      $("shelfError").textContent = "正在抓取网页，请稍候……";
      try {
        const resp = await fetch("/api/web/import", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            url,
            name: $("webNameInput").value.trim(),
          }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          $("shelfError").textContent = data.error || "导入网页失败。";
          return;
        }
        $("shelfError").textContent = "";
        $("webUrlInput").value = "";
        $("webNameInput").value = "";
        await Promise.all([loadShelfList(), loadShelf()]);
      } catch (err) {
        $("shelfError").textContent = "导入网页失败：" + err.message;
      } finally {
        $("importWebBtn").disabled = false;
      }
    }

    $("importWebBtn").addEventListener("click", importWebPage);

    async function loadChapters(path) {
      const select = $("chapterSelect");
      select.innerHTML = "";
      $("chapterQuery").value = "";
      if (!path) return;
      try {
        const resp = await fetch("/api/books/chapters", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path, level: $("chapterLevel").value }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          const opt = document.createElement("option");
          opt.textContent = "无法读取章节";
          opt.disabled = true;
          select.appendChild(opt);
          return;
        }
        data.chapters.forEach((chapter) => {
          const opt = document.createElement("option");
          opt.value = chapter.title;
          opt.textContent = chapter.title;
          select.appendChild(opt);
        });
        allChapterTitles = data.chapters.map((chapter) => chapter.title);
        $("chapterQuery").value = select.value || "";
      } catch (err) {
        const opt = document.createElement("option");
        opt.textContent = "读取章节失败";
        opt.disabled = true;
        select.appendChild(opt);
      }
    }

    $("bookSelect").addEventListener("change", () => {
      const selected = $("bookSelect").value;
      if (selected) loadChapters(JSON.parse(selected).path);
    });

    $("chapterSelect").addEventListener("change", () => {
      $("chapterQuery").value = $("chapterSelect").value;
    });

    function closeChapterMenu() {
      const menu = $("chapterMenu");
      if (menu) menu.classList.add("hidden");
    }

    function renderChapterMenu(filter) {
      const menu = $("chapterMenu");
      if (!menu) return;
      const q = (filter || "").trim().toLowerCase();
      const matches = allChapterTitles.filter(
        (title) => !q || title.toLowerCase().includes(q)
      );
      menu.innerHTML = "";
      if (!matches.length) {
        menu.classList.add("hidden");
        return;
      }
      matches.forEach((title) => {
        const item = document.createElement("div");
        item.className =
          "cm-item" + (title === $("chapterSelect").value ? " selected" : "");
        item.textContent = title;
        item.addEventListener("mousedown", (event) => {
          event.preventDefault();
          $("chapterSelect").value = title;
          $("chapterQuery").value = title;
          menu.classList.add("hidden");
        });
        menu.appendChild(item);
      });
      menu.classList.remove("hidden");
    }

    $("chapterQuery").addEventListener("focus", () =>
      renderChapterMenu($("chapterQuery").value)
    );
    $("chapterQuery").addEventListener("input", () =>
      renderChapterMenu($("chapterQuery").value)
    );
    $("chapterQuery").addEventListener("blur", () =>
      setTimeout(closeChapterMenu, 150)
    );

    $("chapterLevel").addEventListener("change", () => {
      const selected = $("bookSelect").value;
      if (selected) loadChapters(JSON.parse(selected).path);
    });

    $("explainLevel").addEventListener("change", () => {
      localStorage.setItem("explainLevel", $("explainLevel").value);
    });

    async function loadSessions(query) {
      const list = $("sessionList");
      list.innerHTML = "";
      try {
        const url = query
          ? "/api/sessions/search?q=" + encodeURIComponent(query)
          : "/api/sessions";
        const resp = await fetch(url);
        const sessions = await resp.json();
        if (!sessions.length) {
          list.innerHTML = '<p class="empty-hint">' +
            (query ? "没有匹配的会话。" : "还没有已保存的会话。") + "</p>";
          return;
        }
        sessions.forEach((session) => {
          const row = document.createElement("div");
          row.className = "session-row";

          const info = document.createElement("div");
          info.className = "s-info";
          info.innerHTML = '<div class="s-subject"></div><div class="s-meta"></div>';
          const modeLabel = session.mode === "电子书" ? "电子书" : "目录";
          info.querySelector(".s-subject").textContent =
            `[${modeLabel}] ${session.subject}`;
          info.querySelector(".s-meta").textContent =
            `${session.saved_at} · ${session.message_count} 条消息`;

          const actions = document.createElement("div");
          actions.className = "s-actions";

          const resume = document.createElement("button");
          resume.type = "button";
          resume.textContent = "继续";
          resume.addEventListener("click", () => resumeSession(session.id));

          const rename = document.createElement("button");
          rename.type = "button";
          rename.textContent = "重命名";
          rename.addEventListener("click", () => startRename(row, info, session));

          const del = document.createElement("button");
          del.type = "button";
          del.className = "danger";
          del.textContent = "删除";
          del.addEventListener("click", () => confirmDelete(del, session));

          actions.appendChild(resume);
          actions.appendChild(rename);
          actions.appendChild(del);
          row.appendChild(info);
          row.appendChild(actions);
          list.appendChild(row);
        });
      } catch (err) {
        $("historyError").textContent = "读取会话失败：" + err.message;
      }
    }

    let sessionSearchTimer = null;
    $("sessionSearch").addEventListener("input", () => {
      clearTimeout(sessionSearchTimer);
      sessionSearchTimer = setTimeout(
        () => loadSessions($("sessionSearch").value.trim()),
        250
      );
    });

    async function renameSessionRow(session, newSubject) {
      try {
        const resp = await fetch("/api/sessions/rename", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: session.id, subject: newSubject }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          $("historyError").textContent = data.error || "重命名失败。";
          return false;
        }
        await loadSessions($("sessionSearch").value.trim());
        return true;
      } catch (err) {
        $("historyError").textContent = "重命名失败：" + err.message;
        return false;
      }
    }

    function startRename(row, info, session) {
      const subjectDiv = info.querySelector(".s-subject");
      const current = session.subject;
      const input = document.createElement("input");
      input.value = current;
      input.style.width = "100%";
      input.style.padding = "4px 6px";
      subjectDiv.replaceWith(input);
      input.focus();
      let finished = false;
      const finish = async (save) => {
        if (finished) return;
        finished = true;
        const next = input.value.trim();
        if (!save || !next || next === current) {
          await loadSessions($("sessionSearch").value.trim());
          return;
        }
        await renameSessionRow(session, next);
      };
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") finish(true);
        if (event.key === "Escape") finish(false);
      });
      input.addEventListener("blur", () => finish(true));
    }

    async function deleteSessionRow(session) {
      try {
        const resp = await fetch("/api/sessions/delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: session.id }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          $("historyError").textContent = data.error || "删除失败。";
          return;
        }
        await loadSessions($("sessionSearch").value.trim());
      } catch (err) {
        $("historyError").textContent = "删除失败：" + err.message;
      }
    }

    function confirmDelete(button, session) {
      if (button.dataset.armed === "1") {
        deleteSessionRow(session);
        return;
      }
      button.dataset.armed = "1";
      button.textContent = "确认删除?";
      setTimeout(() => {
        button.dataset.armed = "0";
        button.textContent = "删除";
      }, 3000);
    }

    async function loadReview() {
      const box = $("reviewContent");
      box.innerHTML = "";
      try {
        const resp = await fetch("/api/summaries");
        const data = await resp.json();
        const books = data.books || [];
        if (!books.length) {
          box.innerHTML = '<p class="hint">还没有任何学习总结。</p>';
          return;
        }
        books.forEach((book) => {
          const group = document.createElement("div");
          group.className = "book-group";
          const title = document.createElement("h3");
          title.textContent = "📖 " + book.book_name;
          group.appendChild(title);

          book.summaries.forEach((item) => {
            const quizBtn = document.createElement("button");
            quizBtn.type = "button";
            quizBtn.style.cssText = "margin: 6px 0;padding:5px 10px;border:1px solid var(--border);background:#fff;border-radius:8px;cursor:pointer;font-size:13px;";
            quizBtn.textContent = "📝 出题考我";
            quizBtn.addEventListener("click", () =>
              openQuiz(book.book_name, item.chapter_title)
            );

            const details = document.createElement("details");
            details.className = "summary-item";
            const summary = document.createElement("summary");
            summary.textContent = `${item.chapter_title} · ${item.updated_at}`;
            details.appendChild(summary);

            const body = document.createElement("div");
            body.className = "summary-body bubble";
            body.innerHTML = item.html || "";
            details.appendChild(body);
            const wrap = document.createElement("div");
            wrap.style.marginBottom = "8px";
            wrap.appendChild(quizBtn);
            wrap.appendChild(details);
            group.appendChild(wrap);
          });
          box.appendChild(group);
        });
      } catch (err) {
        box.textContent = "读取总结失败：" + err.message;
      }
    }

    function showError(message) {
      $("setupError").textContent = message;
    }

    $("startBtn").addEventListener("click", async () => {
      $("startBtn").disabled = true;
      showError("");
      const payload = { mode };
      payload.explain_level = $("explainLevel").value;
      if (mode === "book") {
        const selected = $("bookSelect").value;
        if (!selected) {
          showError("请先点击「📖 管理书架」添加一本书。");
          $("startBtn").disabled = false;
          return;
        }
        const book = JSON.parse(selected);
        payload.book_name = book.name;
        payload.book_path = book.path;
        payload.chapter_query = $("chapterQuery").value.trim() || $("chapterSelect").value;
        payload.chapter_level = $("chapterLevel").value;
      } else {
        payload.outline_title = $("outlineTitle").value.trim();
        payload.outline_content = $("outlineContent").value.trim();
      }

      try {
        const resp = await fetch("/api/session/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok) {
          showError(data.error || "启动失败");
          return;
        }
        sessionId = data.session_id;
        $("messages").innerHTML = "";
        $("usageLine").textContent = "";
        $("setup").classList.add("hidden");
        $("chatSection").classList.remove("hidden");
        appendAssistant(data.opening_html);
        $("messageInput").focus();
      } catch (err) {
        showError("启动失败：" + err.message);
      } finally {
        $("startBtn").disabled = false;
      }
    });

    function clearRegenerateButtons() {
      document.querySelectorAll(".regenerate-btn").forEach((button) => button.remove());
    }

    function attachRegenerate(wrap) {
      clearRegenerateButtons();
      const button = document.createElement("button");
      button.type = "button";
      button.className = "regenerate-btn";
      button.textContent = "↻ 重新生成";
      button.addEventListener("click", () => regenerateLast(wrap));
      wrap.appendChild(button);
      lastAssistantWrap = wrap;
    }

    function appendUser(text) {
      clearRegenerateButtons();
      lastAssistantWrap = null;
      const wrap = document.createElement("div");
      wrap.className = "msg user";
      wrap.innerHTML = '<div class="role">你</div><div class="bubble"></div>';
      wrap.querySelector(".bubble").textContent = text;
      $("messages").appendChild(wrap);
      scrollToBottom();
    }

    function appendAssistant(html, regenerable = true) {
      const wrap = document.createElement("div");
      wrap.className = "msg assistant";
      wrap.innerHTML = '<div class="role">教练</div><div class="bubble"></div>';
      wrap.querySelector(".bubble").innerHTML = html;
      $("messages").appendChild(wrap);
      if (regenerable) attachRegenerate(wrap);
      scrollToBottom();
    }

    function appendNote(text) {
      const wrap = document.createElement("div");
      wrap.className = "msg assistant";
      wrap.innerHTML = '<div class="role">系统</div><div class="bubble summary-note"></div>';
      wrap.querySelector(".bubble").textContent = text;
      $("messages").appendChild(wrap);
      scrollToBottom();
    }

    function scrollToBottom() {
      const box = $("messages");
      box.scrollTop = box.scrollHeight;
    }

    async function readStream(resp, bubble) {
      if (!resp.ok || !resp.body) {
        bubble.textContent = "请求失败，请稍后重试。";
        return false;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let remainder = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        remainder += decoder.decode(value, { stream: true });
        const lines = remainder.split("\n");
        remainder = lines.pop();
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const payload = line.slice(6).trim();
          if (!payload) continue;
          let event;
          try {
            event = JSON.parse(payload);
          } catch {
            continue;
          }
          if (event.delta) {
            buffer += event.delta;
            bubble.textContent = buffer;
            scrollToBottom();
          } else if (event.done) {
            bubble.innerHTML = event.html;
            if (event.session_usage) updateUsageLine(event.session_usage);
            scrollToBottom();
            return true;
          } else if (event.error) {
            bubble.textContent = "请求失败：" + event.error;
            return false;
          }
        }
      }
      return false;
    }

    async function sendMessage() {
      const input = $("messageInput");
      let text = input.value.trim();
      if (!text || !sessionId) return;
      if (input.dataset.voice === "1") {
        input.dataset.voice = "";
        text =
          "以下是我刚才口头复述的内容，请对照原文指出遗漏、偏差和口头表达的卡壳点，并点评：\n" +
          text;
      }
      input.value = "";
      appendUser(text);
      $("sendBtn").disabled = true;

      const wrap = document.createElement("div");
      wrap.className = "msg assistant";
      wrap.innerHTML = '<div class="role">教练</div><div class="bubble"></div>';
      $("messages").appendChild(wrap);
      const bubble = wrap.querySelector(".bubble");
      scrollToBottom();


      try {
        const resp = await fetch("/api/chat/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionId, message: text }),
        });
        const ok = await readStream(resp, bubble);
        if (ok) attachRegenerate(wrap);
      } catch (err) {
        bubble.textContent = "请求失败：" + err.message;
      } finally {
        $("sendBtn").disabled = false;
        $("messageInput").focus();
      }
    }

    async function regenerateLast(wrap) {
      if (!sessionId) return;
      const bubble = wrap.querySelector(".bubble");
      wrap.querySelectorAll(".regenerate-btn").forEach((button) => button.remove());
      bubble.innerHTML = '<p class="hint">正在重新生成……</p>';
      try {
        const resp = await fetch("/api/chat/regenerate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionId }),
        });
        const ok = await readStream(resp, bubble);
        if (ok) attachRegenerate(wrap);
      } catch (err) {
        bubble.textContent = "请求失败：" + err.message;
      }
    }

    $("sendBtn").addEventListener("click", sendMessage);
    $("messageInput").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
      }
    });

    async function resumeSession(id) {
      if (!id) return;
      $("historyError").textContent = "";
      try {
        const resp = await fetch("/api/session/resume", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          $("historyError").textContent = data.error || "继续失败";
          return;
        }
        sessionId = data.session_id;
        $("messages").innerHTML = "";
        $("usageLine").textContent = "";
        (data.messages || []).forEach((message) => {
          if (message.role === "assistant") {
            appendAssistant(message.html || escapeHtml(message.content));
          } else {
            appendUser(message.content);
          }
        });
        showSection("chatSection");
        $("messageInput").focus();
      } catch (err) {
        $("historyError").textContent = "继续失败：" + err.message;
      }
    }

    $("summaryBtn").addEventListener("click", async () => {
      if (!sessionId) return;
      $("summaryBtn").disabled = true;
      appendNote("正在生成总结……");
      try {
        const resp = await fetch("/api/summary", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionId }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          appendNote(data.error || "生成总结失败");
          return;
        }
        appendAssistant(data.html, false);
        appendNote("总结已保存，下次学习同一章节会自动回顾薄弱点。");
      } catch (err) {
        appendNote("生成总结失败：" + err.message);
      } finally {
        $("summaryBtn").disabled = false;
      }
    });

    $("endBtn").addEventListener("click", async () => {
      if (!sessionId) return;
      $("endBtn").disabled = true;
      try {
        const resp = await fetch("/api/session/end", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionId }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          appendNote(data.error || "结束失败");
          return;
        }
        appendNote("本次会话已保存，学习结束。");
        sessionId = null;
        setTimeout(() => {
          $("chatSection").classList.add("hidden");
          $("setup").classList.remove("hidden");
        }, 600);
      } catch (err) {
        appendNote("结束失败：" + err.message);
      } finally {
        $("endBtn").disabled = false;
      }
    });

    const savedExplainLevel = localStorage.getItem("explainLevel");
    if (savedExplainLevel) $("explainLevel").value = savedExplainLevel;

    function closeSettings() {
      $("settingsModal").classList.add("hidden");
    }

    async function openSettings() {
      try {
        const resp = await fetch("/api/settings");
        const data = await resp.json();
        presetsCache = data.presets || {};
        const select = $("providerSelect");
        select.innerHTML = "";
        Object.entries(presetsCache).forEach(([key, preset]) => {
          const opt = document.createElement("option");
          opt.value = key;
          opt.textContent = preset.name;
          select.appendChild(opt);
        });
        select.value = data.provider || "deepseek";
        $("baseUrlInput").value = data.base_url || "";
        $("modelInput").value = data.model || "";
        $("modelKeyInput").value = "";
        currentAppSettings = {
          spaced_review: !!data.spaced_review,
          price_per_mtok: data.price_per_mtok || 0,
        };
        $("spacedReviewToggle").checked = !!data.spaced_review;
        $("priceInput").value = data.price_per_mtok ? String(data.price_per_mtok) : "";
        $("settingsHint").textContent = data.has_api_key
          ? "当前密钥：" + data.api_key_masked
          : "尚未设置 API Key。";
        $("settingsError").textContent = "";
        $("settingsModal").classList.remove("hidden");
      } catch (err) {
        $("settingsError").textContent = "读取设置失败：" + err.message;
        $("settingsModal").classList.remove("hidden");
      }
    }

    $("settingsBtn").addEventListener("click", openSettings);
    $("closeSettingsBtn").addEventListener("click", closeSettings);
    $("settingsModal").addEventListener("click", (event) => {
      if (event.target === $("settingsModal")) closeSettings();
    });

    $("providerSelect").addEventListener("change", () => {
      const preset = presetsCache[$("providerSelect").value];
      if (preset && preset.base_url) {
        $("baseUrlInput").value = preset.base_url;
        $("modelInput").value = preset.model;
      }
    });

    $("saveSettingsBtn").addEventListener("click", async () => {
      $("saveSettingsBtn").disabled = true;
      $("settingsError").textContent = "";
      try {
        const resp = await fetch("/api/settings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            provider: $("providerSelect").value,
            base_url: $("baseUrlInput").value.trim(),
            model: $("modelInput").value.trim(),
            api_key: $("modelKeyInput").value.trim(),
            spaced_review: $("spacedReviewToggle").checked,
            price_per_mtok: $("priceInput").value || 0,
          }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          $("settingsError").textContent = data.error || "保存失败。";
          return;
        }
        $("modelKeyInput").value = "";
        if (data.api_key_masked) {
          $("settingsHint").textContent = "当前密钥：" + data.api_key_masked;
        }
        currentAppSettings = {
          spaced_review: !!data.spaced_review,
          price_per_mtok: data.price_per_mtok || 0,
        };
        closeSettings();
      } catch (err) {
        $("settingsError").textContent = "保存失败：" + err.message;
      } finally {
        $("saveSettingsBtn").disabled = false;
      }
    });

    // ===== 深色模式 =====
    function currentTheme() {
      const pref = localStorage.getItem("dfc-theme");
      if (pref === "light" || pref === "dark") return pref;
      return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light";
    }
    function applyTheme() {
      const theme = currentTheme();
      document.documentElement.setAttribute("data-theme", theme);
      $("themeBtn").textContent = theme === "dark" ? "☀️ 浅色" : "🌙 深色";
    }
    $("themeBtn").addEventListener("click", () => {
      const theme = currentTheme();
      localStorage.setItem("dfc-theme", theme === "dark" ? "light" : "dark");
      applyTheme();
    });
    if (window.matchMedia) {
      window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
        if (!["light", "dark"].includes(localStorage.getItem("dfc-theme") || "")) {
          applyTheme();
        }
      });
    }
    applyTheme();

    // ===== 版本与更新检查 =====
    async function loadVersion() {
      try {
        const resp = await fetch("/api/version");
        const data = await resp.json();
        $("versionTag").textContent = "v" + data.version;
        checkUpdate(data.version);
      } catch (err) {}
    }
    async function checkUpdate(current) {
      const key = "dfc-update-checked";
      const cached = localStorage.getItem(key);
      if (cached && Date.now() - parseInt(cached, 10) < 24 * 3600 * 1000) return;
      localStorage.setItem(key, String(Date.now()));
      try {
        const resp = await fetch(
          "https://api.github.com/repos/fjfjkk2717821463-cpu/AI-study-coach/releases/latest"
        );
        const data = await resp.json();
        const latest = (data.tag_name || "").replace(/^v/, "");
        if (latest && latest !== current) {
          $("updateText").textContent = "有新版本 v" + latest + " 可用";
          $("updateLink").href = data.html_url || "#";
          $("updateBanner").classList.remove("hidden");
        }
      } catch (err) {}
    }
    $("dismissUpdateBtn").addEventListener("click", () =>
      $("updateBanner").classList.add("hidden")
    );
    loadVersion();

    // ===== 首次使用引导 =====
    const OB_STEPS = [
      {
        title: "① 导入你的书",
        body: "点「📖 管理书架」，导入 .txt / .md / .pdf / .epub，或粘贴网页链接。",
      },
      {
        title: "② 选择章节开始",
        body: "选一本书和章节，教练会先精讲概念：定义 → 直觉 → 例子 → 反例 → 误区。",
      },
      {
        title: "③ 先讲后测",
        body: "听懂后再说「开始检测」，用复述、反例、默写比对巩固。祝你学得扎实！",
      },
    ];
    let obIndex = 0;
    function showObStep() {
      const step = OB_STEPS[obIndex];
      $("obTitle").textContent = step.title;
      $("obBody").innerHTML = '<div class="step">' + step.body + "</div>";
      $("obNext").textContent =
        obIndex === OB_STEPS.length - 1 ? "开始使用" : "下一步";
    }
    function closeOb() {
      $("onboarding").classList.add("hidden");
      localStorage.setItem("dfc-onboarded", "1");
    }
    $("obNext").addEventListener("click", () => {
      if (obIndex >= OB_STEPS.length - 1) {
        closeOb();
        return;
      }
      obIndex += 1;
      showObStep();
    });
    $("obSkip").addEventListener("click", closeOb);
    if (!localStorage.getItem("dfc-onboarded")) {
      $("onboarding").classList.remove("hidden");
      showObStep();
    }

    // ===== 语音复述 =====
    let recognition = null;
    let voiceActive = false;
    function voiceHint(text) {
      const line = $("usageLine");
      if (line) line.textContent = text || "";
    }
    function stopRecording(hint) {
      voiceActive = false;
      const button = $("voiceBtn");
      if (button) {
        button.classList.remove("recording");
        button.textContent = "🎤";
        button.title = "语音复述（说话转文字，发送后教练点评）";
      }
      const input = $("messageInput");
      if (input) input.placeholder = "输入你的理解，教练会追问、给反例、检查逻辑……";
      if (hint) voiceHint(hint);
    }
    $("voiceBtn").addEventListener("click", () => {
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SR) {
        voiceHint(
          "当前窗口不支持语音识别：请用 Safari/Chrome 打开网页版使用（局域网手机访问需先部署到 HTTPS 云端）。"
        );
        return;
      }
      if (voiceActive) {
        try {
          recognition.stop();
        } catch (err) {}
        return;
      }
      recognition = new SR();
      recognition.lang = "zh-CN";
      recognition.interimResults = true;
      recognition.continuous = true;
      recognition.maxAlternatives = 1;
      recognition.onstart = () => {
        voiceActive = true;
        const button = $("voiceBtn");
        button.classList.add("recording");
        button.textContent = "⏹";
        button.title = "点击停止录音";
        voiceHint("🎤 正在录音……说完了点 ⏹ 停止");
        $("messageInput").placeholder = "正在听你说……";
      };
      recognition.onresult = (event) => {
        let finalText = "";
        let interim = "";
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const result = event.results[i];
          if (result.isFinal) finalText += result[0].transcript;
          else interim += result[0].transcript;
        }
        const value = finalText || interim;
        if (value) {
          $("messageInput").value = value;
          $("messageInput").dataset.voice = "1";
        }
      };
      recognition.onerror = (event) => {
        const name = (event && event.error) || "unknown";
        stopRecording(
          name === "not-allowed" || name === "service-not-allowed"
            ? "❌ 麦克风/语音权限被拒绝：请到「系统设置 → 隐私与安全性 → 麦克风 / 语音识别」允许 DFL Coach。"
            : "❌ 语音识别出错（" + name + "）：请重试，或改用 Safari/Chrome 网页版。"
        );
      };
      recognition.onend = () => {
        const input = $("messageInput");
        if (voiceActive) {
          voiceHint(
            input && input.value
              ? "已转成文字，点「发送」后教练会点评你的口头复述。"
              : "未识别到声音，请确认麦克风已开启后重试。"
          );
        }
        stopRecording();
      };
      try {
        recognition.start();
      } catch (err) {
        stopRecording("无法启动语音识别：" + err.message + "（请检查麦克风权限）");
      }
    });

    // ===== 用量显示 =====
    function updateUsageLine(usage) {
      if (!usage || !usage.total_tokens) {
        $("usageLine").textContent = "";
        return;
      }
      const price = currentAppSettings.price_per_mtok || 0;
      const tokens = usage.total_tokens || 0;
      let text = "本次会话 tokens：" + tokens;
      if (price > 0) {
        text += " · 估算费用约 ¥" + ((tokens / 1e6) * price).toFixed(3);
      }
      $("usageLine").textContent = text;
    }

    // ===== 默写模式 =====
    $("compareBtn").addEventListener("click", openCompare);
    $("closeCompareBtn").addEventListener("click", () =>
      $("compareModal").classList.add("hidden")
    );
    async function openCompare() {
      if (!sessionId) return;
      $("reconstructionInput").value = "";
      $("sourceText").textContent = "";
      $("compareOutput").classList.add("hidden");
      $("compareOutput").innerHTML = "";
      $("sourceDetails").removeAttribute("open");
      $("compareModal").classList.remove("hidden");
      try {
        const resp = await fetch(
          "/api/session/source?session_id=" + encodeURIComponent(sessionId)
        );
        const data = await resp.json();
        $("sourceText").textContent =
          data.text ||
          "（目录速建模式没有原文，将基于教练已有知识点评）";
      } catch (err) {}
    }
    $("startCompareBtn").addEventListener("click", async () => {
      const text = $("reconstructionInput").value.trim();
      if (!text) return;
      $("startCompareBtn").disabled = true;
      const bubble = $("compareOutput");
      bubble.classList.remove("hidden");
      bubble.innerHTML = '<p class="hint">正在比对……</p>';
      try {
        const resp = await fetch("/api/session/compare", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionId, reconstruction: text }),
        });
        const ok = await readStream(resp, bubble);
        if (ok) {
          appendUser("【默写重构】\n" + text);
          const wrap = document.createElement("div");
          wrap.className = "msg assistant";
          wrap.innerHTML = '<div class="role">教练</div><div class="bubble"></div>';
          wrap.querySelector(".bubble").innerHTML = bubble.innerHTML;
          $("messages").appendChild(wrap);
          attachRegenerate(wrap);
          scrollToBottom();
        }
      } catch (err) {
        bubble.textContent = "请求失败：" + err.message;
      } finally {
        $("startCompareBtn").disabled = false;
      }
    });

    // ===== 薄弱点测验 =====
    let currentQuiz = null;
    $("closeQuizBtn").addEventListener("click", () =>
      $("quizModal").classList.add("hidden")
    );
    async function openQuiz(bookName, chapterTitle) {
      currentQuiz = { book_name: bookName, chapter_title: chapterTitle, questions: [], answers: {} };
      $("quizModal").classList.remove("hidden");
      $("quizMeta").textContent = bookName + " · " + chapterTitle + "（正在出题……）";
      $("quizBody").innerHTML = "";
      $("quizResult").classList.add("hidden");
      $("quizResult").innerHTML = "";
      try {
        const resp = await fetch("/api/quiz/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ book_name: bookName, chapter_title: chapterTitle }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          $("quizMeta").textContent = data.error || "出题失败";
          return;
        }
        currentQuiz.questions = data.questions || [];
        $("quizMeta").textContent = bookName + " · " + chapterTitle;
        renderQuiz();
      } catch (err) {
        $("quizMeta").textContent = "出题失败：" + err.message;
      }
    }
    function renderQuiz() {
      const box = $("quizBody");
      box.innerHTML = "";
      currentQuiz.questions.forEach((question, index) => {
        const wrap = document.createElement("div");
        wrap.className = "quiz-question";
        const title = document.createElement("div");
        title.className = "q-title";
        title.textContent = index + 1 + ". " + question.question;
        wrap.appendChild(title);
        if (question.type === "choice") {
          question.options.forEach((opt) => {
            const label = document.createElement("div");
            label.className = "quiz-option";
            label.textContent = opt;
            label.addEventListener("click", () => {
              currentQuiz.answers[String(index + 1)] = opt;
              Array.from(wrap.querySelectorAll(".quiz-option")).forEach((o) =>
                o.classList.remove("selected")
              );
              label.classList.add("selected");
            });
            wrap.appendChild(label);
          });
        } else {
          const input = document.createElement("textarea");
          input.style.cssText =
            "width:100%;min-height:64px;border:1px solid var(--border);border-radius:9px;padding:8px;font-size:14px;background:var(--card);color:var(--text);";
          input.addEventListener("input", () => {
            currentQuiz.answers[String(index + 1)] = input.value;
          });
          wrap.appendChild(input);
        }
        box.appendChild(wrap);
      });
    }
    $("submitQuizBtn").addEventListener("click", async () => {
      if (!currentQuiz || !currentQuiz.questions.length) return;
      $("submitQuizBtn").disabled = true;
      $("quizResult").classList.remove("hidden");
      $("quizResult").innerHTML = '<p class="hint">正在批改……</p>';
      try {
        const resp = await fetch("/api/quiz/grade", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            questions: currentQuiz.questions,
            answers: currentQuiz.answers,
          }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          $("quizResult").textContent = data.error || "批改失败";
          return;
        }
        $("quizResult").innerHTML = data.html || "";
      } catch (err) {
        $("quizResult").textContent = "批改失败：" + err.message;
      } finally {
        $("submitQuizBtn").disabled = false;
      }
    });

    // ===== 间隔复习 =====
    async function loadDueReviews() {
      const box = $("dueReviews");
      box.innerHTML = "";
      try {
        const resp = await fetch("/api/reviews/due");
        const data = await resp.json();
        if (!data.enabled) return;
        if (!data.items.length) {
          box.innerHTML = '<p class="hint">📅 今天没有到期复习（已开启间隔复习）。</p>';
          return;
        }
        const title = document.createElement("h3");
        title.textContent = "📅 今日待复习";
        box.appendChild(title);
        data.items.forEach((item) => {
          const row = document.createElement("div");
          row.className = "review-due-row";
          const info = document.createElement("div");
          info.textContent = item.book_name + " · " + item.chapter_title;
          const actions = document.createElement("div");
          actions.className = "r-actions";
          [["忘了", 1], ["模糊", 2], ["掌握", 3]].forEach(([label, quality]) => {
            const button = document.createElement("button");
            button.textContent = label;
            button.addEventListener("click", async () => {
              button.disabled = true;
              try {
                await fetch("/api/reviews/record", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({
                    book_name: item.book_name,
                    chapter_title: item.chapter_title,
                    quality,
                  }),
                });
              } catch (err) {}
              await loadDueReviews();
            });
            actions.appendChild(button);
          });
          row.appendChild(info);
          row.appendChild(actions);
          box.appendChild(row);
        });
      } catch (err) {}
    }

    // ===== 概念图谱 =====
    async function loadConceptMap() {
      const box = $("conceptMapContent");
      box.innerHTML = "";
      try {
        const resp = await fetch("/api/summaries");
        const data = await resp.json();
        const books = data.books || [];
        if (!books.length) {
          box.innerHTML = '<p class="hint">还没有学习总结。先学习并生成总结，概念会自动汇总到这里。</p>';
          return;
        }
        books.forEach((book) => {
          const group = document.createElement("div");
          group.className = "book-group";
          const title = document.createElement("h3");
          title.textContent = "📖 " + book.book_name;
          group.appendChild(title);

          const tags = document.createElement("div");
          const seen = new Set();
          let hasConcept = false;
          book.summaries.forEach((item) => {
            (item.concepts || []).forEach((concept) => {
              const key = concept.term + concept.level;
              if (seen.has(key)) return;
              seen.add(key);
              hasConcept = true;
              const tag = document.createElement("span");
              tag.className =
                "concept-tag " + (concept.level === "掌握" ? "mastered" : "weak");
              tag.textContent = concept.term + " · " + concept.level;
              tag.title = item.chapter_title;
              tags.appendChild(tag);
            });
          });
          if (!hasConcept) {
            const hint = document.createElement("p");
            hint.className = "hint";
            hint.textContent = "这本书还没有概念清单。";
            group.appendChild(hint);
          } else {
            group.appendChild(tags);
          }

          const extractBtn = document.createElement("button");
          extractBtn.type = "button";
          extractBtn.textContent = "⚡ 补充提取概念";
          extractBtn.style.cssText =
            "margin-top:10px;padding:6px 12px;border:1px solid var(--border);background:#fff;border-radius:8px;cursor:pointer;font-size:13px;";
          extractBtn.addEventListener("click", async () => {
            extractBtn.disabled = true;
            extractBtn.textContent = "正在提取（可能需要一会儿）……";
            try {
              const resp = await fetch("/api/concept-map", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ book_name: book.book_name }),
              });
              const result = await resp.json();
              if (!resp.ok) {
                extractBtn.textContent = result.error || "提取失败";
                extractBtn.disabled = false;
                return;
              }
              await loadConceptMap();
            } catch (err) {
              extractBtn.textContent = "提取失败";
              extractBtn.disabled = false;
            }
          });
          group.appendChild(extractBtn);
          box.appendChild(group);
        });
      } catch (err) {
        box.textContent = "读取概念图谱失败：" + err.message;
      }
    }

    loadShelf();

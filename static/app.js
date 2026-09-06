    let mode = "book";
    let sessionId = null;
    let allChapterTitles = [];
    let lastAssistantWrap = null;
    let presetsCache = {};

    const $ = (id) => document.getElementById(id);

    function escapeHtml(text) {
      const div = document.createElement("div");
      div.textContent = text == null ? "" : String(text);
      return div.innerHTML;
    }

    function showSection(name) {
      ["setup", "chatSection", "bookshelfSection", "historySection", "reviewSection"].forEach((id) => {
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
      await loadReview();
    });

    $("shelfBtn").addEventListener("click", async () => {
      showSection("bookshelfSection");
      $("shelfError").textContent = "";
      await loadShelfList();
    });

    $("historyBackBtn").addEventListener("click", () => showSection("setup"));
    $("reviewBackBtn").addEventListener("click", () => showSection("setup"));
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
            const details = document.createElement("details");
            details.className = "summary-item";
            const summary = document.createElement("summary");
            summary.textContent = `${item.chapter_title} · ${item.updated_at}`;
            details.appendChild(summary);

            const body = document.createElement("div");
            body.className = "summary-body bubble";
            body.innerHTML = item.html || "";
            details.appendChild(body);
            group.appendChild(details);
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
      const text = input.value.trim();
      if (!text || !sessionId) return;
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
        closeSettings();
      } catch (err) {
        $("settingsError").textContent = "保存失败：" + err.message;
      } finally {
        $("saveSettingsBtn").disabled = false;
      }
    });

    loadShelf();

/* 支付凭单生成台 — 前端交互 */
(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  let toastTimer = null;
  function toast(message, isError = false) {
    const box = $("#toast");
    if (!box) return;
    box.textContent = message;
    box.classList.toggle("error", isError);
    box.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => box.classList.remove("show"), 2800);
  }

  async function api(url, options = {}) {
    const opts = Object.assign({ headers: { "Content-Type": "application/json" } }, options);
    if (opts.body && typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
    const res = await fetch(url, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "请求失败：" + res.status);
    return data;
  }

  const val = (id) => {
    const el = document.getElementById(id);
    return el ? el.value.trim() : "";
  };

  /* 选择这些费用类型时，按考勤测算默认填充金额 */
  const AUTO_FEE_TYPES = ["工资", "技术服务费"];
  /* 月份统一为「YYYY年M月」（含年份标志） */
  const defaultMonth = () => {
    const d = new Date();
    return d.getFullYear() + "年" + (d.getMonth() + 1) + "月";
  };
  /* 月份选择器值 "2026-10" → "2026年10月"；其他写法原样返回 */
  const formatMonth = (value) => {
    const text = String(value || "").trim();
    const m = text.match(/^(\d{4})[-/.](\d{1,2})$/);
    return m ? m[1] + "年" + parseInt(m[2], 10) + "月" : text;
  };
  /* "2026年10月" / "10月" → 选择器值 "2026-10"；无法识别返回空串 */
  const monthToInput = (value) => {
    const text = String(value || "").trim();
    let m = text.match(/^(\d{4})[-/.](\d{1,2})$/);
    if (m) return m[1] + "-" + String(parseInt(m[2], 10)).padStart(2, "0");
    m = text.match(/^(\d{4})年(\d{1,2})月$/);
    if (m) return m[1] + "-" + String(parseInt(m[2], 10)).padStart(2, "0");
    m = text.match(/^(\d{1,2})月$/);
    if (m) return new Date().getFullYear() + "-" + String(parseInt(m[1], 10)).padStart(2, "0");
    return "";
  };
  const fmtAmount = (value) => {
    const num = Number(value);
    if (!isFinite(num)) return "";
    return String(parseFloat(num.toFixed(2)));
  };

  /* ------------------------------------------------------------ 制单页 */

  function initForm() {
    const form = $("#voucherForm");
    const slot = $("#previewSlot");
    const pill = $("#previewState");
    const submitBtn = $("#submitBtn");
    const saveTplBtn = $("#saveTemplateBtn");
    const recordId = $("#recordId");
    const personId = $("#personId");

    const fields = [
      "name", "identity", "hire_date", "contract_end", "bank_name", "bank_account",
      "fee_type", "amount", "period_month", "voucher_date",
    ];
    let timer = null;
    let personsCache = null;
    let mode = window.__MODE__ || "create";
    let salaryOverride = null; // 模板或考勤测算带出的薪资，不在页面显示

    const collect = () => ({
      id: recordId.value || null,
      name: val("name"),
      identity: val("identity"),
      hire_date: val("hire_date"),
      contract_end: val("contract_end"),
      bank_name: val("bank_name"),
      bank_account: val("bank_account"),
      fee_type: val("fee_type"),
      salary: salaryOverride,
      amount: val("amount"),
      period_month: formatMonth(val("period_month")),
      voucher_date: val("voucher_date"),
      person_id: personId.value || null,
    });

    /* 费用类型为工资/技术服务费时，按考勤测算默认填充金额 */
    async function maybeFillAmount(silent = false) {
      const fee = val("fee_type");
      if (!AUTO_FEE_TYPES.includes(fee) || !personId.value) return;
      const month = formatMonth(val("period_month")) || defaultMonth();
      try {
        const res = await api(
          "/api/attendance/amount?person_id=" + encodeURIComponent(personId.value) +
          "&month=" + encodeURIComponent(month)
        );
        if (!res.found) return;
        const amountEl = document.getElementById("amount");
        if (amountEl) amountEl.value = res.amount;
        salaryOverride = res.salary || null;
        renderPreview();
        if (!silent) toast("已按 " + month + " 考勤测算填充金额：" + res.amount);
      } catch (err) { /* 无考勤数据时静默跳过 */ }
    }

    const feeSelect = document.getElementById("fee_type");
    if (feeSelect) feeSelect.addEventListener("change", () => maybeFillAmount());

    const placeholder = (text) => {
      slot.innerHTML = '<div class="paper placeholder">' + text + "</div>";
    };

    async function renderPreview() {
      const data = collect();
      if (!(parseFloat(data.amount) > 0)) {
        placeholder("填写支付金额后在此呈现凭单");
        pill.textContent = "待填写";
        pill.classList.remove("live");
        return;
      }
      try {
        const res = await api("/api/preview", { method: "POST", body: data });
        slot.innerHTML = res.html;
        const no = res.context.serial_no;
        pill.textContent = no ? "第 " + no + " 号" : "已预览";
        pill.classList.add("live");
      } catch (err) {
        toast(err.message, true);
      }
    }

    const debounced = () => {
      clearTimeout(timer);
      timer = setTimeout(renderPreview, 320);
    };

    form.addEventListener("input", debounced);
    form.addEventListener("change", debounced);

    function setMode(next) {
      mode = next;
      const title = $("#modeTitle");
      const hint = $("#modeHint");
      if (next === "edit") {
        title.textContent = "修改凭单";
        hint.textContent = "调整细项后保存，凭单编号保持不变。";
        submitBtn.textContent = "保存并生成 docx";
        saveTplBtn.hidden = false;
      } else if (next === "template") {
        title.textContent = "按模板制单";
        hint.textContent = "人员与账户信息来自模板，仅可调整费用类型、金额、月份与事由。";
        saveTplBtn.hidden = true;
      } else {
        title.textContent = "新制凭单";
        hint.textContent = "填写收款信息，右侧实时预览版式，确认无误后生成 docx 文件。";
        submitBtn.textContent = "生成凭单并下载";
        saveTplBtn.hidden = false;
      }
    }

    function fill(record) {
      fields.forEach((key) => {
        const el = document.getElementById(key);
        if (!el) return;
        let value = record[key];
        if (value === null || value === undefined) value = "";
        if (key === "voucher_date" && value) value = String(value).slice(0, 10);
        if (key === "hire_date" || key === "contract_end") value = value ? String(value).slice(0, 10) : "";
        el.value = value;
      });
      personId.value = record.person_id || "";
    }

    async function loadVoucher(id) {
      try {
        const record = await api("/api/vouchers/" + id);
        fill(record);
        salaryOverride = record.salary || null;
        recordId.value = record.id;
        $("#serialNo").value = record.serial_no ? "第 " + record.serial_no + " 号" : "自动编号";
        setMode("edit");
        renderPreview();
      } catch (err) {
        toast(err.message, true);
      }
    }

    async function loadTemplate(id) {
      try {
        const record = await api("/api/templates/" + id);
        fill(record);
        salaryOverride = record.salary || null;
        recordId.value = "";
        ["name", "identity", "hire_date", "contract_end", "bank_name", "bank_account"].forEach((key) => {
          const el = document.getElementById(key);
          if (el) {
            el.readOnly = true;
            el.classList.add("readonly");
            if (el.tagName === "SELECT") el.disabled = true;
          }
        });
        setMode("template");
        renderPreview();
      } catch (err) {
        toast(err.message, true);
      }
    }

    async function loadPersons() {
      if (!personsCache) {
        const data = await api("/api/persons");
        personsCache = data;
      }
      return personsCache;
    }

    /* 切回「手动填写」：清空人员填充信息并恢复默认值 */
    const resetManual = () => {
      ["name", "identity", "hire_date", "contract_end", "bank_name", "bank_account"].forEach((key) => {
        const el = document.getElementById(key);
        if (!el) return;
        el.readOnly = false;
        el.classList.remove("readonly");
        if (el.tagName === "SELECT") el.disabled = false;
        el.value = key === "bank_name" ? "xxxxxx" : key === "bank_account" ? "1234567890" : "";
      });
      const amountEl = document.getElementById("amount");
      if (amountEl) amountEl.value = "0";
      personId.value = "";
      salaryOverride = null;
      setMode("create");
      renderPreview();
    };

    $("#personPick").addEventListener("change", async (event) => {
      const id = event.target.value;
      if (!id) {
        resetManual();
        return;
      }
      try {
        const persons = await loadPersons();
        const person = persons.find((item) => String(item.id) === String(id));
        if (!person) return;
        fill(person);
        personId.value = person.id;
        salaryOverride = null;
        renderPreview();
        toast("已填入 " + person.name + " 的收款信息");
        maybeFillAmount();
      } catch (err) {
        toast(err.message, true);
      }
    });

    $$("[data-add]").forEach((button) => {
      button.addEventListener("click", async () => {
        const kind = button.dataset.add;
        const label = kind === "identity" ? "身份" : "费用类型";
        const name = window.prompt("新增" + label + "名称");
        if (!name || !name.trim()) return;
        try {
          const row = await api(kind === "identity" ? "/api/identities" : "/api/fee-types", {
            method: "POST",
            body: { name: name.trim() },
          });
          const select = document.getElementById(kind === "identity" ? "identity" : "fee_type");
          select.appendChild(new Option(row.name, row.name, true, true));
          renderPreview();
          toast("已添加" + label + "：" + row.name);
        } catch (err) {
          toast(err.message, true);
        }
      });
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = collect();
      if (!data.name) return toast("请填写姓名", true);
      if (!data.identity) return toast("请选择身份", true);
      if (!data.fee_type) return toast("请选择费用类型", true);
      if (!(parseFloat(data.amount) > 0)) return toast("支付金额必须大于 0", true);

      submitBtn.disabled = true;
      try {
        const record = recordId.value
          ? await api("/api/vouchers/" + recordId.value, { method: "PUT", body: data })
          : await api("/api/vouchers", { method: "POST", body: data });
        recordId.value = record.id;
        $("#serialNo").value = "第 " + record.serial_no + " 号";
        setMode("edit");
        toast("已生成凭单，正在下载 docx");
        window.location.href = "/api/vouchers/" + record.id + "/docx";
        renderPreview();
      } catch (err) {
        toast(err.message, true);
      } finally {
        submitBtn.disabled = false;
      }
    });

    saveTplBtn.addEventListener("click", async () => {
      const data = collect();
      if (!data.name) return toast("请先填写姓名", true);
      try {
        const title = window.prompt("模板名称", data.name + " · " + data.identity + " 模板");
        if (title === null) return;
        const record = await api("/api/templates", {
          method: "POST",
          body: Object.assign({}, data, { title: title || data.name + " 模板", id: null }),
        });
        toast("已保存模板：" + record.title);
      } catch (err) {
        toast(err.message, true);
      }
    });

    $("#previewBtn").addEventListener("click", renderPreview);

    const params = new URLSearchParams(window.location.search);
    const vid = params.get("voucher_id");
    const tid = params.get("template_id");
    if (vid) loadVoucher(vid);
    else if (tid) loadTemplate(tid);
    else placeholder("填写支付金额后在此呈现凭单");
  }

  /* ------------------------------------------------------------ 记录页 */

  function initHistory() {
    $$("[data-del]").forEach((button) => {
      button.addEventListener("click", async () => {
        if (!window.confirm("删除该凭单记录？此操作不可撤销。")) return;
        try {
          await api("/api/vouchers/" + button.dataset.del, { method: "DELETE" });
          button.closest("tr").remove();
          toast("已删除");
        } catch (err) {
          toast(err.message, true);
        }
      });
    });
  }

  /* ------------------------------------------------------------ 模板页 */

  function initTemplates() {
    const dialog = $("#tplDialog");
    const form = $("#tplForm");
    let current = null;

    $$("[data-edit]").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          current = await api("/api/templates/" + button.dataset.edit);
          $("#tplId").value = current.id;
          $("#tplTitle").value = current.title || "";
          $("#tplFeeType").value = current.fee_type || "";
          $("#tplSalary").value = current.salary ?? "";
          $("#tplPeriod").value = monthToInput(current.period_month);
          $("#tplBankAccount").value = current.bank_account || "1234567890";
          $("#tplBankName").value = current.bank_name || "xxxxxx";
          $("#tplAmount").value = current.amount ?? 0;
          $("#tplDialogHint").textContent =
            "人员：" + current.name + "（" + current.identity + "），账户与人员信息保持不变。";
          dialog.showModal();
        } catch (err) {
          toast(err.message, true);
        }
      });
    });

    if (dialog) {
      $("[data-close]", dialog).addEventListener("click", () => dialog.close());
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!current) return;
        const body = Object.assign({}, current, {
          title: $("#tplTitle").value.trim(),
          fee_type: $("#tplFeeType").value,
          salary: $("#tplSalary").value.trim(),
          amount: $("#tplAmount").value.trim(),
          bank_account: $("#tplBankAccount").value.trim(),
          bank_name: $("#tplBankName").value.trim(),
          period_month: formatMonth($("#tplPeriod").value),
        });
        try {
          await api("/api/templates/" + current.id, { method: "PUT", body });
          dialog.close();
          toast("模板已更新");
          setTimeout(() => window.location.reload(), 500);
        } catch (err) {
          toast(err.message, true);
        }
      });
    }

    $$("[data-del]").forEach((button) => {
      button.addEventListener("click", async () => {
        if (!window.confirm("删除该模板？")) return;
        try {
          await api("/api/templates/" + button.dataset.del, { method: "DELETE" });
          button.closest(".tpl-card").remove();
          toast("已删除模板");
        } catch (err) {
          toast(err.message, true);
        }
      });
    });
  }

  /* ------------------------------------------------------------ 设置页 */

  function bindChipRemove(chip) {
    $(".chip-x", chip).addEventListener("click", async () => {
      const kind = chip.dataset.kind;
      try {
        await api("/api/" + (kind === "identity" ? "identities" : "fee-types") + "/" + chip.dataset.id, {
          method: "DELETE",
        });
        chip.remove();
        toast("已删除");
      } catch (err) {
        toast(err.message, true);
      }
    });
  }

  function initSettings() {
    $$(".chip").forEach(bindChipRemove);

    $$(".inline-form").forEach((form) => {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const input = $("input", form);
        const name = input.value.trim();
        if (!name) return;
        const kind = form.dataset.kind;
        try {
          const row = await api(kind === "identity" ? "/api/identities" : "/api/fee-types", {
            method: "POST",
            body: { name },
          });
          const container = kind === "identity" ? $("#identityChips") : $("#feeChips");
          const chip = document.createElement("span");
          chip.className = "chip";
          chip.dataset.kind = kind;
          chip.dataset.id = row.id;
          chip.innerHTML = row.name + '<button class="chip-x" title="删除">×</button>';
          bindChipRemove(chip);
          container.appendChild(chip);
          input.value = "";
          toast("已添加：" + row.name);
        } catch (err) {
          toast(err.message, true);
        }
      });
    });

    /* 编辑人员 */
    const personDialog = $("#personDialog");
    const personForm = $("#personForm");
    if (personDialog && personForm) {
      $$("[data-edit-person]").forEach((button) => {
        button.addEventListener("click", () => {
          const tr = button.closest("tr");
          if (!tr) return;
          $("#personEditId").value = tr.dataset.personId || "";
          $("#personEditName").value = tr.dataset.name || "";
          $("#personEditIdentity").value = tr.dataset.identity || "";
          $("#personEditSalary").value = tr.dataset.salary || "";
          $("#personEditHire").value = tr.dataset.hire || "";
          $("#personEditContract").value = tr.dataset.contract || "";
          $("#personEditBank").value = tr.dataset.bankName || "";
          $("#personEditAccount").value = tr.dataset.bankAccount || "";
          personDialog.showModal();
        });
      });
      $("[data-close]", personDialog).addEventListener("click", () => personDialog.close());
      personForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const id = $("#personEditId").value;
        const body = {
          name: $("#personEditName").value.trim(),
          identity: $("#personEditIdentity").value,
          salary: $("#personEditSalary").value.trim() || null,
          hire_date: $("#personEditHire").value || null,
          contract_end: $("#personEditContract").value || null,
          bank_name: $("#personEditBank").value.trim(),
          bank_account: $("#personEditAccount").value.trim(),
        };
        try {
          await api("/api/persons/" + id, { method: "PUT", body });
          toast("人员信息已保存");
          setTimeout(() => window.location.reload(), 500);
        } catch (err) {
          toast(err.message, true);
        }
      });
    }

    $$("[data-del-person]").forEach((button) => {
      button.addEventListener("click", async () => {
        if (!window.confirm("从人员库删除该人员？")) return;
        try {
          await api("/api/persons/" + button.dataset.delPerson, { method: "DELETE" });
          button.closest("tr").remove();
          toast("已删除");
        } catch (err) {
          toast(err.message, true);
        }
      });
    });

    const importBtn = $('[data-action="import"]');
    const importFile = $("#importFile");
    if (importBtn && importFile) {
      importBtn.addEventListener("click", () => importFile.click());
      importFile.addEventListener("change", async () => {
        const file = importFile.files[0];
        if (!file) return;
        const form = new FormData();
        form.append("file", file);
        importBtn.disabled = true;
        toast("正在导入 " + file.name + " …");
        try {
          const res = await fetch("/api/persons/import-file", { method: "POST", body: form });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.error || "导入失败：" + res.status);
          toast("已从 " + file.name + " 导入 " + data.imported + " 条人员记录");
          setTimeout(() => window.location.reload(), 600);
        } catch (err) {
          toast(err.message, true);
        } finally {
          importBtn.disabled = false;
          importFile.value = "";
        }
      });
    }

    initDbConfig();
  }

  /* ------------------------------------------------------------ 考勤页 */

  function initAttendance() {
    const monthInput = $("#attMonth");
    $("#attSwitch")?.addEventListener("click", () => {
      const month = formatMonth(monthInput?.value);
      if (!month) return toast("请选择考勤月份", true);
      window.location.href = "/attendance?month=" + encodeURIComponent(month);
    });
    $("#attExport")?.addEventListener("click", () => {
      const month = formatMonth(monthInput?.value) || window.__MONTH__ || defaultMonth();
      window.location.href = "/api/attendance/export?month=" + encodeURIComponent(month);
    });

    /* 备注显示与导出一致：有奖金时备注栏同步显示「奖金 N；用户备注」，
       保存时剥离奖金前缀，奖金仍单独存列。 */
    const withBonusNote = (note, bonus) =>
      bonus > 0 ? "奖金 " + fmtAmount(bonus) + (note ? "；" + note : "") : note;
    const stripBonusNote = (text) =>
      String(text || "").replace(/^奖金\s*[\d.,]+；?/, "").trim();

    $$(".att-row").forEach((row) => {
      const inputs = $$(".att-input", row);
      const noteInput = $(".att-note", row);
      const computed = {
        online_amount: $('[data-computed="online_amount"]', row),
        offline_amount: $('[data-computed="offline_amount"]', row),
        total_amount: $('[data-computed="total_amount"]', row),
      };
      const get = (field) => {
        const el = inputs.find((item) => item.dataset.field === field);
        return el ? parseFloat(el.value) || 0 : 0;
      };
      const syncNote = () => {
        if (!noteInput) return;
        const userNote = stripBonusNote(noteInput.value);
        noteInput.value = withBonusNote(userNote, get("bonus"));
      };
      const recalc = () => {
        const expected = get("expected_days");
        const onlineAmt = expected > 0 ? (1500 * get("online_days")) / expected : 0;
        const offlineAmt = expected > 0 ? (get("salary") * get("offline_days")) / expected : 0;
        computed.online_amount.textContent = fmtAmount(onlineAmt) || "0";
        computed.offline_amount.textContent = fmtAmount(offlineAmt) || "0";
        computed.total_amount.textContent = fmtAmount(onlineAmt + offlineAmt + get("bonus")) || "0";
      };
      let timer = null;
      const save = async () => {
        const body = {
          person_id: row.dataset.personId,
          period_month: formatMonth(monthInput?.value) || window.__MONTH__ || defaultMonth(),
        };
        inputs.forEach((input) => {
          body[input.dataset.field] =
            input.dataset.field === "note"
              ? stripBonusNote(input.value)
              : parseFloat(input.value) || 0;
        });
        try {
          const res = await api("/api/attendance", { method: "POST", body });
          computed.online_amount.textContent = fmtAmount(res.online_amount);
          computed.offline_amount.textContent = fmtAmount(res.offline_amount);
          computed.total_amount.textContent = fmtAmount(res.total_amount);
          if (noteInput) noteInput.value = withBonusNote(res.note || "", res.bonus || 0);
          toast(row.dataset.name + " 的考勤已保存");
        } catch (err) {
          toast(err.message, true);
        }
      };
      /* 初始加载：备注栏同步显示奖金 */
      syncNote();
      inputs.forEach((input) => {
        input.addEventListener("input", () => {
          if (input.dataset.field === "bonus") syncNote();
          recalc();
          clearTimeout(timer);
          timer = setTimeout(save, 800);
        });
      });
    });
  }

  /* ------------------------------------------------------------ 批量生成页 */

  function initBatch() {
    const monthInput = $("#batchMonth");
    const dateInput = $("#batchDate");
    const rows = $$(".batch-row");

    $("#batchAll")?.addEventListener("change", (event) => {
      $$(".batch-check").forEach((check) => { check.checked = event.target.checked; });
    });

    const autoFill = async (row) => {
      const fee = $(".batch-fee", row).value;
      if (!AUTO_FEE_TYPES.includes(fee)) return false;
      const month = formatMonth(monthInput?.value) || window.__MONTH__ || defaultMonth();
      try {
        const res = await api(
          "/api/attendance/amount?person_id=" + encodeURIComponent(row.dataset.personId) +
          "&month=" + encodeURIComponent(month)
        );
        if (res.found) {
          $(".batch-amount", row).value = res.amount;
          return true;
        }
      } catch (err) { /* 无数据时跳过 */ }
      return false;
    };

    rows.forEach((row) => {
      $(".batch-fee", row).addEventListener("change", () => autoFill(row));
    });

    $("#batchFillBtn")?.addEventListener("click", async () => {
      let filled = 0;
      for (const row of rows) {
        if (await autoFill(row)) filled += 1;
      }
      toast(filled ? "已按考勤填充 " + filled + " 行金额" : "没有找到可填充的考勤数据", !filled);
    });

    $("#batchGenBtn")?.addEventListener("click", async () => {
      const month = formatMonth(monthInput?.value) || window.__MONTH__ || defaultMonth();
      const items = [];
      for (const row of rows) {
        if (!$(".batch-check", row).checked) continue;
        const amount = $(".batch-amount", row).value.trim();
        if (!amount) {
          toast(row.dataset.name + " 未填写支付金额", true);
          return;
        }
        items.push({
          person_id: row.dataset.personId,
          name: row.dataset.name,
          identity: row.dataset.identity,
          hire_date: row.dataset.hire || null,
          contract_end: row.dataset.contract || null,
          bank_name: row.dataset.bankName || "",
          bank_account: row.dataset.bankAccount || "",
          fee_type: $(".batch-fee", row).value,
          amount,
          period_month: month,
          voucher_date: (dateInput?.value || "").trim() || null,
        });
      }
      if (!items.length) return toast("请先勾选要生成凭单的人员", true);
      const btn = $("#batchGenBtn");
      const state = $("#batchState");
      btn.disabled = true;
      state.textContent = "正在生成 " + items.length + " 张凭单…";
      try {
        const res = await api("/api/vouchers/batch", { method: "POST", body: { items } });
        toast("已生成 " + res.created.length + " 张凭单，开始下载合并文件");
        state.textContent = "完成：共 " + res.created.length + " 张凭单已合并到一个 docx。";
        window.location.href = res.download;
      } catch (err) {
        toast(err.message, true);
        state.textContent = "";
      } finally {
        btn.disabled = false;
      }
    });
  }

  function initDbConfig() {
    const form = $("#dbConfigForm");
    if (!form) return;
    const state = $("#dbConfigState");

    // 回填当前连接信息（密码不回显）
    api("/api/db-config")
      .then((cfg) => {
        form.host.value = cfg.host || "";
        form.port.value = cfg.port || "";
        form.dbname.value = cfg.dbname || "";
        form.user.value = cfg.user || "";
        if (!cfg.configured) state.textContent = "尚未配置，填写后保存即可连接。";
      })
      .catch(() => {});

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      state.className = "hint";
      state.textContent = "正在保存并测试连接…";
      try {
        const res = await api("/api/db-config", {
          method: "POST",
          body: {
            host: form.host.value.trim(),
            port: form.port.value.trim(),
            dbname: form.dbname.value.trim(),
            user: form.user.value.trim(),
            password: form.password.value,
          },
        });
        if (res.connected) {
          state.className = "hint ok";
          state.textContent = "已保存（加密存储），连接成功。";
          toast("数据库连接成功");
          setTimeout(() => window.location.reload(), 800);
        } else {
          state.className = "hint err";
          state.textContent = "已保存（加密存储），但连接失败：" + res.error;
        }
      } catch (err) {
        state.className = "hint err";
        state.textContent = err.message;
      }
    });
  }

  /* ------------------------------------------------------------ 启动 */

  document.addEventListener("DOMContentLoaded", () => {
    const page = window.__PAGE__ || "index";
    if (page === "history") initHistory();
    else if (page === "templates") initTemplates();
    else if (page === "settings") initSettings();
    else if (page === "attendance") initAttendance();
    else if (page === "batch") initBatch();
    else initForm();
  });
})();

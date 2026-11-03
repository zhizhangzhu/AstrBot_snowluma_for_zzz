/* Unified data migration. All writes require a fresh server-side preview. */
(() => {
  "use strict";
  const LABELS = { companion: "陪伴配置、画像与日记", persona: "AstrBot 人格正文", memory: "长期记忆、关系与时间线", history: "真实经历、兴趣进展与信息来源", resources: "陪伴图片资源", memes: "表情图库、标签与语义" };
  const REQUIRED = new Set(["companion", "persona", "memory"]);
  let providerDraft = {};
  let api, status, archiveId = "", selected = null, preview = null, manifest = null, busy = false;
  const byId = (id) => document.getElementById(id);
  const text = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const message = (value, error = false) => { const box = byId("portableMessage"); box.textContent = value; box.setAttribute("role", error ? "alert" : "status"); box.dataset.error = error ? "1" : "0"; };
  function invalidate() { preview = null; byId("portableApply").disabled = true; }
  function busyState(value) {
    busy = value;
    for (const id of ["portableExport", "portableFile", "portablePreview", "portableRefresh", "portableResume"]) byId(id).disabled = value || (id === "portablePreview" && !archiveId);
    byId("portableApply").disabled = value || !preview?.can_import;
  }
  async function act(operation) {
    if (busy) return;
    busyState(true);
    try { await operation(); } catch (error) { invalidate(); message(error.message || String(error), true); }
    finally { busyState(false); }
  }
  function target() { return { platform_id: byId("portableTargetPlatform").value.trim(), bot_id: byId("portableTargetQQ").value.trim(), persona_id: byId("portableTargetPersona").value.trim() }; }
  function options() {
    const providerMap = {...providerDraft};
    document.querySelectorAll("[data-portable-provider]").forEach((input) => { if (input.value) providerMap[input.dataset.portableProvider] = input.value; });
    providerDraft = {...providerMap};
    return { archive_id: archiveId, mode: byId("portableMode").value, target: target(), components: selected, conflict: byId("portableConflict").value, provider_map: providerMap };
  }
  function components(values, dependencies = []) {
    const old = new Map(dependencies.map((item) => [item.component, item]));
    byId("portableModules").innerHTML = Object.keys(values).map((key) => {
      const dependency = old.get(key), required = dependency ? dependency.required : REQUIRED.has(key);
      const included = selected === null || selected.includes(key);
      return `<label class="portable-module"><input type="checkbox" data-portable-module="${text(key)}" ${included ? "checked" : ""} ${required ? "disabled" : ""}><span><b>${text(LABELS[key] || key)}</b>${required ? " · 必需" : " · 可选"}<small>${dependency ? text((dependency.ready ? "依赖就绪" : dependency.reason) + "；" + dependency.plugin + " " + (dependency.installed_version || "未安装") + "；要求 " + dependency.required_version) : "导入前会检查对应插件与版本"}</small>${dependency?.install_url && !dependency.ready ? `<a href="${text(dependency.install_url)}" target="_blank" rel="noopener noreferrer">查看插件仓库与版本</a>` : ""}</span></label>`;
    }).join("");
  }
  function renderPreview(value) {
    const box = byId("portablePreviewResult");
    box.innerHTML = `<div class="portable-result ${value.can_import ? "ready" : "blocked"}"><b>${value.can_import ? "检查通过，可以确认导入" : "条件尚未满足，不能导入"}</b>${(value.blockers || []).map((line) => `<p>${text(line)}</p>`).join("")}<p>目标：QQ ${text(value.target.bot_id)} · ${text(value.target.persona_id)}</p><p>长期记忆模块记录 ${Number(value.records?.memory || 0)} 条；经历与来源记录 ${Number(value.records?.history || 0)} 条；资源 ${Number(value.resource_files || 0)} 个。</p><p>冲突处理：${value.conflict === "use_archive" ? "使用档案内容，导入前自动备份" : "保留当前已有内容，只补充缺少记录"}。</p>${Object.keys(value.database_checks?.conflicts || {}).length ? `<p>检测到 ${Object.values(value.database_checks.conflicts).reduce((a,b) => a + Number(b), 0)} 条已有记录存在差异，按所选冲突策略处理。</p>` : ""}<p>暂不导入：${text((value.skipped_components || []).map((k) => LABELS[k] || k).join("、") || "无")}</p>${Object.values(value.omitted_records || {}).some((dbs) => Object.keys(dbs).length) ? '<p>属于其它档案或无法确认归属的记录已留在原环境，不会混入当前人格；记录数量保存在导出清单中。</p>' : ''}${(value.dependencies || []).filter((d) => d.runtime_only).map((d) => `<p>运行配置依赖：${text(d.label)}，要求 ${text(d.required_version)}，${text(d.ready ? '已就绪' : d.reason)}。${d.install_url ? `<a href="${text(d.install_url)}" target="_blank" rel="noopener noreferrer">查看插件仓库</a>` : ''}</p>`).join('')}${(value.notes || []).map((line) => `<p>${text(line)}</p>`).join("")}</div>`;
    components(manifest.components, value.dependencies);
    const mappings = byId("portableProviderMap");
    if (value.missing_provider_ids?.length) {
      mappings.innerHTML = `<h3>需要映射的模型</h3>${value.missing_provider_ids.map((id) => `<label>${text(id)}<select data-portable-provider="${text(id)}"><option value="">请选择本机提供商</option>${(status?.providers || []).map((p) => `<option value="${text(p.id)}">${text(p.label || p.id)}</option>`).join("")}</select></label>`).join("")}`;
    }
    byId("portableApply").disabled = !value.can_import;
  }
  async function refresh() {
    status = await api.get("/portability/status");
    byId("portableStatus").textContent = status.recovery_pending ? "存在未完成事务，自动行为已锁定，请先回滚" : status.paused ? "迁移后的自动行为暂停中" : "当前没有迁移暂停";
    byId("portableResume").hidden = !status.paused || status.recovery_pending;
    byId("portableResume").dataset.scope = status.source ? JSON.stringify([status.source.platform_id, status.source.bot_id, status.source.persona_id]) : "";
    if (!archiveId && status.source) {
      byId("portableTargetPlatform").value = status.source.platform_id;
      byId("portableTargetQQ").value = status.source.bot_id;
      byId("portableTargetPersona").value = status.source.persona_id;
    }
    byId("portableBackups").innerHTML = (status.backups || []).map((row) => `<div class="portable-backup"><span>${text(new Date(row.created * 1000).toLocaleString())} · ${text(row.target?.persona_id)} · ${text(({committed:"已导入",rolled_back:"已回滚",prepared:"准备中",applying:"未完成",rollback_failed:"回滚待处理"})[row.state] || row.state)}</span><button type="button" data-portable-rollback="${text(row.id)}" ${row.state === "rolled_back" ? "disabled" : ""}>恢复迁移前备份</button></div>`).join("") || "暂无迁移备份。";
  }
  function bytesToBase64(bytes) { let binary = ""; for (let i = 0; i < bytes.length; i += 32768) binary += String.fromCharCode(...bytes.subarray(i, i + 32768)); return btoa(binary); }
  async function chooseFile(file) {
    invalidate(); archiveId = ""; manifest = null; selected = null; providerDraft = {};
    if (!file) return;
    if (!status) await refresh();
    if (file.size > status.max_bytes) throw new Error("文件超过128MiB，请减少可选资源后重新导出");
    const upload = await api.post("/portability/upload/start", { bytes: file.size });
    for (let offset = 0; offset < file.size; offset += upload.chunk_bytes) {
      message(`正在上传并校验档案 ${Math.round(offset / file.size * 100)}%`);
      const bytes = new Uint8Array(await file.slice(offset, offset + upload.chunk_bytes).arrayBuffer());
      await api.post("/portability/upload/chunk", { archive_id: upload.archive_id, offset, data: bytesToBase64(bytes) });
    }
    const result = await api.post("/portability/inspect", { archive_id: upload.archive_id });
    archiveId = upload.archive_id; manifest = result;
    if (byId("portableMode").value === "host") {
      byId("portableTargetPlatform").value = manifest.source.platform_id;
      byId("portableTargetQQ").value = manifest.source.bot_id;
      byId("portableTargetPersona").value = manifest.source.persona_id;
    }
    components(manifest.components);
    byId("portableProviderMap").replaceChildren();
    byId("portablePreviewResult").textContent = `档案来自 QQ ${manifest.source.bot_id} · ${manifest.source.persona_id}，请检查目标绑定并点击预览。`;
    message("文件校验完成；预览不会写入人格或记忆。");
  }
  function bootstrap(config) {
    api = config;
    const panel = byId("portableMigrationPanel");
    if (!panel || panel.dataset.bound) return;
    panel.dataset.bound = "1";
    panel.addEventListener("toggle", () => { if (panel.open && !status) act(refresh); });
    byId("portableRefresh").addEventListener("click", () => act(refresh));
    byId("portableFile").addEventListener("change", (event) => act(() => chooseFile(event.target.files[0])));
    byId("portableExport").addEventListener("click", () => act(async () => {
      const selectedComponents = [...panel.querySelectorAll("[data-portable-export]:checked")].map((x) => x.dataset.portableExport);
      message("正在收集档案、校验依赖与资源……");
      const result = await api.post("/portability/export", { components: selectedComponents });
      const parts = [];
      for (let offset = 0; offset < result.bytes; offset += status?.chunk_bytes || 1048576) {
        const chunk = await api.get(`/portability/download?archive_id=${encodeURIComponent(result.archive_id)}&offset=${offset}`);
        const raw = atob(chunk.data); parts.push(Uint8Array.from(raw, (c) => c.charCodeAt(0)));
        message(`正在下载档案 ${Math.min(100, Math.round((offset + raw.length) / result.bytes * 100))}%`);
      }
      const url = URL.createObjectURL(new Blob(parts, {type:"application/zip"}));
      const link = document.createElement("a"); link.href=url; link.download=result.filename; link.click(); setTimeout(() => URL.revokeObjectURL(url),60000);
      message("档案已导出，包含私人记忆，请保存在你自己的设备中。");
    }));
    byId("portablePreview").addEventListener("click", () => act(async () => {
      invalidate(); const value = await api.post("/portability/preview", options()); preview=value;renderPreview(value);
      message(value.can_import ? "预览通过；尚未写入，确认后才会导入。" : "请按检查结果补齐依赖、映射模型，或取消缺少依赖的可选模块。", !value.can_import);
    }));
    byId("portableApply").addEventListener("click", () => act(async () => {
      if (!preview?.preview_token) throw new Error("请先重新预览");
      const request = {...options(), preview_token:preview.preview_token}; invalidate();
      message("正在备份并导入，请保留此页面……");
      const result=await api.post("/portability/apply",request);message(result.message);await refresh();
      byId("portablePreviewResult").textContent = `导入完成。目标 QQ ${result.target.bot_id} · ${result.target.persona_id}。请重新加载以下插件后检查：${(result.requires_reload_plugins || []).join("、") || "无"}。自动行为保持暂停，正常聊天可继续。`;
    }));
    byId("portableResume").addEventListener("click", () => act(async () => {
      const scope=byId("portableResume").dataset.scope;
      if (!scope) throw new Error("当前QQ账号尚未明确，不能恢复");
      await api.post("/portability/resume",{scope});message("已恢复当前账号与人格的自动行为，继续遵守原有开关和额度。");await refresh();
    }));
    panel.addEventListener("change", (event) => {
      if (event.target.matches("#portableFile,[data-portable-export]")) return;
      if (event.target.matches("[data-portable-module]")) selected=[...panel.querySelectorAll("[data-portable-module]:checked")].map((x) => x.dataset.portableModule);
      invalidate();
    });
    panel.addEventListener("input", (event) => { if (event.target.matches("[data-portable-target]")) invalidate(); });
    panel.addEventListener("click", (event) => {
      const button=event.target.closest("[data-portable-rollback]");
      if (!button) return;
      if (!window.confirm("恢复这次迁移前的备份？后续已修改的数据会被保护，存在冲突时停止回滚。")) return;
      act(async () => { await api.post("/portability/rollback",{transaction_id:button.dataset.portableRollback});invalidate();message("已恢复迁移前数据，请重新加载受影响插件并检查。");await refresh(); });
    });
  }
  window.PrivateCompanionMigrationPanel = { bootstrap };
})();

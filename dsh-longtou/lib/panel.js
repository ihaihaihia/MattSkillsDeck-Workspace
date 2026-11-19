(function () {
  if (window.__dshLongtouPanel) return;
  window.__dshLongtouPanel = true;

  const latestUrl = window.__DSH_LONGTOU_LATEST_URL || '/dsh-longtou/latest.md';

  /* ===== 候选流通市值标红阈值（单位：亿，与「流通市值(亿)」列同单位）=====
     两个方向都算异常端：> 90 亿（盘子太大，接力困难）或 < 30 亿（盘子太小，容易被操纵/承接差）。
     落在 [30, 90] 区间内不标红；空值 / 非数字不标红。只影响显示，不动排序与数值格式化。 */
  const CAP_RED_HIGH_YI = 90;
  const CAP_RED_LOW_YI = 30;
  // 判定：入参是单元格原始文本（形如 "175.19"）。能解析成有限数字且落在区间外才返回 true。
  function isCapOutlierYi(text) {
    const value = Number.parseFloat(String(text == null ? '' : text).replace(/[,\s]/g, ''));
    if (!Number.isFinite(value)) return false;
    return value > CAP_RED_HIGH_YI || value < CAP_RED_LOW_YI;
  }
  const css = document.createElement('style');
  css.textContent = `
  /* ===== 颜色变量层：默认暗色，:root[data-theme="light"] 覆盖为亮色 =====
     面板是 fixed 浮窗而不是整页，所以「亮色」只换浮窗内部底色，不动宿主页面。 */
  :root{
    --lt-bg:#121212;--lt-panel:#1e1e1e;--lt-surface:#232323;--lt-surface-2:#252525;
    --lt-card:#262626;--lt-card-2:#232323;--lt-pop-bg:#1e1e1e;
    --lt-th-bg:#2a2a2a;--lt-th-text:#9a9a9a;--lt-border:#3a3a3a;--lt-border-soft:#333333;
    --lt-panel-border:#3a3a3a;
    --lt-text:#e0e0e0;--lt-text-2:#d5d5df;--lt-text-3:#bcbcc8;--lt-strong:#ffffff;
    --lt-muted:#888888;--lt-faint:#707070;--lt-code:#9a9aa8;
    --lt-up:#ff5252;--lt-up-soft:#ff6b6b;--lt-up-strong:#ff8a8a;--lt-up-text:#ffb3b3;
    --lt-up-deep:#b30000;--lt-up-deep-bg:#8b0000;--lt-up-dark:#3a0b10;--lt-up-dark-2:#230609;
    /* 量比两档红：单独一套变量，比通用红再提两档（更艳更醒目），按主题各给一份 */
    --lt-ratio-warm:#ff7090;--lt-ratio-hot:#ff3b3b;
    /* 断板反包叠加层（连板梯队列）：亮粉，刻意区别于普通涨停红，一眼看出是「断板后再连板」 */
    --lt-overlay-pink:#ff4fb8;
    --lt-down:#66bb6a;--lt-down-strong:#6fe0a8;--lt-down-text:#a9f3c6;
    --lt-info:#84a9ff;--lt-warn:#f0b35b;--lt-temp:#ffb300;--lt-highlight:#4a3f00;
    --lt-line:#ff5252;--lt-bar:#5c2a2e;--lt-bar-down:#2e4a33;--lt-grid:rgba(255,255,255,.10);
    /* 悬停竖点划线的颜色：默认（暗色）白色，亮色主题覆盖成黑色，见下面的 :root[data-theme="light"] */
    --lt-cv-cursor:#ffffff;
    --lt-purple:#a78bfa;
    /* 高亮深紫：只给梯队 4 板色阶用，紫虚线/图例圆点仍走 --lt-purple（淡薰衣草） */
    --lt-violet:#b026ff;
    --lt-btn-bg:#2a2a2a;--lt-btn-bg-hover:#333333;--lt-btn-border:#3a3a3a;--lt-btn-border-hover:#4a4a58;
    --lt-gold:#f4c96b;--lt-gold-2:#f6d68a;--lt-gold-3:#b8862f;--lt-gold-soft:#d9c28a;
    --lt-gold-line:#8f621d;--lt-gold-bg:#1e1b14;--lt-gold-border:#3a3222;--lt-gold-border-2:#6b5324;
    --lt-on-gold:#17120a;--lt-tip-bg:#211d14;--lt-tip-text:#eadfc6;--lt-tip-border:#6b5324;
    --lt-shadow:rgba(0,0,0,.62);--lt-shadow-soft:rgba(0,0,0,.45);--lt-hairline:rgba(255,255,255,.03);
    --lt-gold-tint-weak:rgba(244,201,107,.05);--lt-gold-tint:rgba(244,201,107,.1);--lt-gold-glow:rgba(244,201,107,.09);
    --lt-up-tint-1:rgba(255,107,107,.06);--lt-up-tint-2:rgba(255,107,107,.1);--lt-up-tint-3:rgba(255,107,107,.22);
    --lt-down-tint-1:rgba(86,214,160,.06);--lt-down-tint-2:rgba(86,214,160,.1);
    --lt-down-tint-3:rgba(86,214,160,.28);--lt-down-glow:rgba(86,214,160,.6);
    --lt-row-eligible-bg:rgba(255,110,110,.14);--lt-row-eligible-bg-hover:rgba(255,110,110,.22);
    --lt-row-watch-bg:rgba(86,214,160,.14);--lt-row-watch-bg-hover:rgba(86,214,160,.22);
    --lt-highest-bg-1:rgba(139,0,0,.62);--lt-highest-bg-2:rgba(139,0,0,.2);--lt-amount-glow:rgba(179,0,0,.45);
    --lt-muted-tint:rgba(142,142,155,.06);
    --lt-info-tint-1:rgba(132,169,255,.06);--lt-info-tint-2:rgba(132,169,255,.28);
    --lt-warn-tint-1:rgba(240,179,91,.07);--lt-warn-tint-2:rgba(240,179,91,.2);
    --lt-scroll:#3b3b47;--lt-skeleton:#2a2a35;
  }
  :root[data-theme="light"]{
    --lt-bg:#ffffff;--lt-panel:#ffffff;--lt-surface:#ffffff;--lt-surface-2:#fafafa;
    --lt-card:#f7f7f7;--lt-card-2:#f2f2f2;--lt-pop-bg:#ffffff;
    --lt-th-bg:#f5f5f5;--lt-th-text:#666666;--lt-border:#e0e0e0;--lt-border-soft:#eeeeee;
    --lt-panel-border:#e0e0e0;
    --lt-text:#333333;--lt-text-2:#444444;--lt-text-3:#555555;--lt-strong:#111111;
    --lt-muted:#888888;--lt-faint:#999999;--lt-code:#666666;
    --lt-up:#e53935;--lt-up-soft:#d32f2f;--lt-up-strong:#c62828;--lt-up-text:#c62828;
    --lt-up-deep:#b71c1c;--lt-up-deep-bg:#ef9a9a;--lt-up-dark:#fff5f5;--lt-up-dark-2:#ffebee;
    --lt-ratio-warm:#e8445c;--lt-ratio-hot:#d32f2f;--lt-overlay-pink:#e6007a;
    --lt-down:#43a047;--lt-down-strong:#2e7d32;--lt-down-text:#2e7d32;
    --lt-info:#1565c0;--lt-warn:#e65100;--lt-temp:#f9a825;--lt-highlight:#fff9c4;
    --lt-line:#d32f2f;--lt-bar:#ffcdd2;--lt-bar-down:#c8e6c9;--lt-grid:rgba(0,0,0,.10);
    /* 亮色主题：悬停竖点划线改成黑色，保证白底上也看得见 */
    --lt-cv-cursor:#000000;
    --lt-purple:#7c3aed;
    --lt-violet:#7b1fa2;
    --lt-btn-bg:#eeeeee;--lt-btn-bg-hover:#e3e3e3;--lt-btn-border:#e0e0e0;--lt-btn-border-hover:#cfcfcf;
    --lt-gold:#a67c00;--lt-gold-2:#c8a03c;--lt-gold-3:#d9bd7a;--lt-gold-soft:#8a6a1f;
    --lt-gold-line:#c8a03c;--lt-gold-bg:#fff8e1;--lt-gold-border:#e8d9a8;--lt-gold-border-2:#dcc97a;
    --lt-on-gold:#ffffff;--lt-tip-bg:#fffbe6;--lt-tip-text:#5b4a1a;--lt-tip-border:#dcc97a;
    --lt-shadow:rgba(0,0,0,.16);--lt-shadow-soft:rgba(0,0,0,.10);--lt-hairline:rgba(0,0,0,.05);
    --lt-gold-tint-weak:rgba(166,124,0,.06);--lt-gold-tint:rgba(166,124,0,.12);--lt-gold-glow:rgba(166,124,0,.08);
    --lt-up-tint-1:rgba(229,57,53,.06);--lt-up-tint-2:rgba(229,57,53,.10);--lt-up-tint-3:rgba(229,57,53,.18);
    --lt-down-tint-1:rgba(67,160,71,.08);--lt-down-tint-2:rgba(67,160,71,.12);
    --lt-down-tint-3:rgba(67,160,71,.20);--lt-down-glow:rgba(67,160,71,.35);
    --lt-row-eligible-bg:#ffebee;--lt-row-eligible-bg-hover:#ffcdd2;
    --lt-row-watch-bg:#e8f5e9;--lt-row-watch-bg-hover:#c8e6c9;
    --lt-highest-bg-1:rgba(229,57,53,.20);--lt-highest-bg-2:rgba(229,57,53,.08);--lt-amount-glow:rgba(183,28,28,.25);
    --lt-muted-tint:rgba(136,136,136,.08);
    --lt-info-tint-1:rgba(21,101,192,.08);--lt-info-tint-2:rgba(21,101,192,.22);
    --lt-warn-tint-1:rgba(230,81,0,.08);--lt-warn-tint-2:rgba(230,81,0,.22);
    --lt-scroll:#cccccc;--lt-skeleton:#eaeaea;
  }
  #dsh-longtou-fab{position:fixed;left:18px;bottom:86px;z-index:2147483000;width:48px;height:48px;border-radius:15px;
    border:1px solid var(--lt-gold-line);background:radial-gradient(circle at 30% 20%,var(--lt-gold-bg),var(--lt-bg) 72%);
    color:var(--lt-gold);font:800 19px/1 "Segoe UI","Microsoft YaHei",sans-serif;cursor:pointer;
    box-shadow:0 10px 28px var(--lt-shadow-soft),inset 0 0 0 1px var(--lt-hairline);transition:transform .18s ease,box-shadow .18s ease}
  #dsh-longtou-fab:hover{transform:translateY(-2px);box-shadow:0 14px 34px var(--lt-shadow),0 0 0 4px var(--lt-gold-tint)}
  #dsh-longtou-fab{cursor:grab;touch-action:none}
  #dsh-longtou-fab.lt-fab-dragging{cursor:grabbing;transform:none;transition:none}
  #dsh-longtou-fab::after{content:"";position:absolute;inset:-4px;border-radius:19px;border:1px solid var(--lt-gold-tint);animation:lt-pulse 3.2s ease-in-out infinite}
  @keyframes lt-pulse{0%,100%{opacity:.35;transform:scale(1)}50%{opacity:.9;transform:scale(1.03)}}
  #dsh-longtou-panel{position:fixed;left:76px;bottom:86px;z-index:2147483001;width:min(1240px,calc(100vw - 100px));
    height:min(780px,calc(100vh - 110px));display:none;flex-direction:column;overflow:hidden;border-radius:18px;
    border:1px solid var(--lt-panel-border);background:
      radial-gradient(900px 300px at 0% 0%,var(--lt-gold-glow),transparent 62%),
      radial-gradient(800px 280px at 100% 0%,var(--lt-info-tint-1),transparent 60%),
      var(--lt-panel);
    box-shadow:0 28px 80px var(--lt-shadow);color:var(--lt-text);
    font-family:"Segoe UI Variable","Segoe UI","Microsoft YaHei","PingFang SC",sans-serif}
  #dsh-longtou-panel.open{display:flex}
  #dsh-longtou-panel.lt-max{left:16px !important;top:16px !important;right:auto !important;bottom:auto !important;
    width:calc(100vw - 32px) !important;height:calc(100vh - 32px) !important;border-radius:15px}
  #dsh-longtou-panel.lt-min{height:auto !important;min-height:0 !important}
  #dsh-longtou-panel.lt-min .lt-nav,
  #dsh-longtou-panel.lt-min .lt-body,
  #dsh-longtou-panel.lt-min .lt-foot,
  #dsh-longtou-panel.lt-min .lt-resize{display:none !important}
  #dsh-longtou-panel.lt-min .lt-head{border-bottom:none}
  #dsh-longtou-panel.lt-min .lt-head-actions #dsh-longtou-copy,
  #dsh-longtou-panel.lt-min .lt-head-actions #dsh-longtou-refresh{display:none}
  #dsh-longtou-tip{position:fixed;z-index:2147483647;max-width:300px;padding:9px 11px;display:none;
    background:var(--lt-tip-bg);color:var(--lt-tip-text);border:1px solid var(--lt-gold-border-2);border-radius:9px;
    box-shadow:0 10px 28px var(--lt-shadow-soft);font:12px/1.65 "Segoe UI","Microsoft YaHei",sans-serif;pointer-events:none}
  #dsh-longtou-tip.show{display:block}
  /* 角落提示条（自动补刷 / 刷新结果）：右下角小条，6 秒后淡出，pointer-events:none 不挡点击 */
  #dsh-longtou-toast{position:fixed;right:18px;bottom:24px;z-index:2147483640;max-width:340px;padding:9px 13px;
    border:1px solid var(--lt-gold-border-2);border-radius:10px;background:var(--lt-tip-bg);color:var(--lt-tip-text);
    font:12px/1.65 "Segoe UI","Microsoft YaHei",sans-serif;box-shadow:0 12px 30px var(--lt-shadow-soft);
    opacity:0;transform:translateY(6px);transition:opacity .2s ease,transform .2s ease;pointer-events:none}
  #dsh-longtou-toast.lt-in{opacity:1;transform:none}
  .lt-tip{cursor:help;border-bottom:1px dotted var(--lt-gold-line);white-space:nowrap}
  .lt-click-tip{cursor:pointer;border-bottom:1px dotted var(--lt-gold-line);white-space:nowrap}
  /* ▾ 只做提示装饰，不参与行内居中：用负右边距抵消自身宽度，表头文字才真正居中，
     箭头伸进 th 的右侧 padding（10px），不会压到下一列表头 */
  .lt-tip-mark{font-size:9px;color:var(--lt-gold);margin-left:2px;margin-right:-10px}
  .lt-head{display:flex;align-items:center;gap:12px;padding:16px 20px;border-bottom:1px solid var(--lt-border);
    background:linear-gradient(180deg,var(--lt-gold-tint-weak),transparent);flex:0 0 auto;
    cursor:grab;touch-action:none;user-select:none}
  .lt-head .lt-btn,.lt-head .lt-date{cursor:pointer}
  #dsh-longtou-panel.lt-dragging .lt-head{cursor:grabbing}
  #dsh-longtou-panel.lt-dragging{user-select:none}
  #dsh-longtou-panel.lt-max .lt-head{cursor:default}
  #dsh-longtou-panel.lt-resizing,#dsh-longtou-panel.lt-resizing *{user-select:none}
  .lt-resize{position:absolute;z-index:9;touch-action:none;background:transparent}
  .lt-resize:hover{background:var(--lt-gold-tint)}
  .lt-resize-n{top:0;left:14px;right:14px;height:7px;cursor:ns-resize}
  .lt-resize-s{bottom:0;left:14px;right:14px;height:7px;cursor:ns-resize}
  .lt-resize-e{top:14px;right:0;bottom:14px;width:7px;cursor:ew-resize}
  .lt-resize-w{top:14px;left:0;bottom:14px;width:7px;cursor:ew-resize}
  .lt-resize-nw{top:0;left:0;width:15px;height:15px;cursor:nwse-resize}
  .lt-resize-ne{top:0;right:0;width:15px;height:15px;cursor:nesw-resize}
  .lt-resize-sw{bottom:0;left:0;width:15px;height:15px;cursor:nesw-resize}
  .lt-grip{right:4px;bottom:4px;width:19px;height:19px;cursor:nwse-resize;z-index:10}
  .lt-grip::before{content:"";position:absolute;right:4px;bottom:4px;width:10px;height:10px;
    border-right:2px solid var(--lt-gold);border-bottom:2px solid var(--lt-gold);border-radius:2px;opacity:.55}
  #dsh-longtou-panel.lt-max .lt-resize{display:none}
  #dsh-longtou-panel.lt-snapped{animation:lt-snap .28s ease-out}
  @keyframes lt-snap{0%{transform:scale(.985);opacity:.85}100%{transform:scale(1);opacity:1}}
  .lt-brand{display:flex;align-items:center;gap:11px;min-width:0}
  .lt-brand-mark{width:38px;height:38px;border-radius:12px;display:grid;place-items:center;flex:0 0 auto;
    background:linear-gradient(135deg,var(--lt-gold-2),var(--lt-gold-3));color:var(--lt-on-gold);font-weight:900;font-size:17px;
    box-shadow:0 4px 14px var(--lt-gold-tint)}
  .lt-title{font-size:17px;font-weight:750;letter-spacing:.4px;line-height:1.2}
  .lt-sub{font-size:10px;color:var(--lt-muted);letter-spacing:1.6px;text-transform:uppercase;margin-top:2px}
  .lt-head-actions{margin-left:auto;display:flex;align-items:center;gap:7px;min-width:0}
  /* 窗口置顶按钮的激活态（仅改按钮外观，不做页内 z-index 层级） */
  #dsh-longtou-panel.lt-on-top #dsh-longtou-pin{color:var(--lt-on-gold);background:var(--lt-gold);border-color:var(--lt-gold)}
  .lt-nav{display:flex;align-items:center;gap:8px;padding:7px 20px;border-bottom:1px solid var(--lt-border);
    background:var(--lt-surface-2);flex:0 0 auto}
  .lt-nav .lt-btn{padding:4px 9px}
  .lt-pos{font-family:ui-monospace,"Cascadia Code",Consolas,monospace;font-size:10.5px;color:var(--lt-muted);min-width:58px;text-align:center}
  .lt-pos b{color:var(--lt-gold)}
  .lt-nav .lt-spacer{flex:1}
  /* 自动刷新时间表：工具栏右侧一行小字；面板变窄时收缩截断，不挤动按钮 */
  .lt-auto{flex:0 1 auto;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;
    font-family:"Segoe UI","Microsoft YaHei",sans-serif;font-size:10.5px;color:var(--lt-muted);
    letter-spacing:.2px;cursor:help}
  #dsh-longtou-panel.lt-narrow .lt-auto{font-size:9.5px;letter-spacing:0}
  .lt-date{font-family:ui-monospace,"Cascadia Code",Consolas,monospace;font-size:11px;color:var(--lt-gold);
    background:var(--lt-gold-bg);border:1px solid var(--lt-gold-border);border-radius:999px;padding:5px 10px;white-space:nowrap}
  .lt-btn{background:var(--lt-btn-bg);border:1px solid var(--lt-btn-border);color:var(--lt-text-2);border-radius:8px;padding:6px 10px;
    cursor:pointer;font:600 11px "Segoe UI","Microsoft YaHei",sans-serif;transition:background .15s,border-color .15s}
  .lt-btn:hover{background:var(--lt-btn-bg-hover);border-color:var(--lt-btn-border-hover)}
  .lt-btn:disabled{opacity:.4;cursor:default}
  .lt-icon{width:30px;padding:6px 0;font-size:16px;line-height:1}
  .lt-body{flex:1 1 auto;overflow:auto;padding:18px 20px 28px;scrollbar-color:var(--lt-scroll) transparent}
  .lt-body::-webkit-scrollbar{width:8px;height:8px}
  .lt-body::-webkit-scrollbar-thumb{background:var(--lt-scroll);border-radius:8px}
  .lt-body h2{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--lt-gold);letter-spacing:1.5px;
    text-transform:uppercase;margin:24px 0 10px;font-weight:700}
  .lt-body h2:first-child{margin-top:2px}
  .lt-body h2::before{content:"";width:4px;height:14px;border-radius:2px;background:linear-gradient(var(--lt-gold-2),var(--lt-gold-line))}
  .lt-body h3{display:flex;align-items:center;gap:8px;margin:18px 0 7px;color:var(--lt-gold-soft);
    font-size:12px;letter-spacing:1px;font-weight:700}
  .lt-body h3::before{content:"";width:18px;height:1px;background:linear-gradient(90deg,var(--lt-gold),transparent)}
  .lt-body h3:first-child{margin-top:2px}
  .lt-metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px}
  .lt-metric{background:linear-gradient(180deg,var(--lt-card),var(--lt-card-2));border:1px solid var(--lt-border);border-radius:11px;
    padding:11px 13px;font-size:12px;line-height:1.7;color:var(--lt-text-3)}
  .lt-metric strong{color:var(--lt-strong);font-family:ui-monospace,"Cascadia Code",Consolas,monospace;font-size:13px;font-weight:700}
  /* lt-out 复用 lt-top 的红色底，语义不同：lt-top = 「最高板」强调，lt-out = 指标超出经验区间 */
  .lt-metric.lt-top,.lt-metric.lt-out{background:linear-gradient(180deg,var(--lt-up-dark),var(--lt-up-dark-2));border-color:var(--lt-up-deep-bg);color:var(--lt-up-text);
    box-shadow:inset 0 0 0 1px var(--lt-up-tint-1)}
  .lt-metric.lt-top strong,.lt-metric.lt-out strong{color:var(--lt-up-strong)}
  /* 指标卡第二行：可接受区间小字（区间来自 renderMetrics 上方的 METRIC_RANGES 经验表） */
  .lt-metric .lt-range{margin-top:3px;font-size:10px;line-height:1.6;color:var(--lt-muted)}
  .lt-metric .lt-range.lt-range-out{color:var(--lt-up-strong);font-weight:700}
  /* 阶段表下方的排序口径脚注：从简，不参与表格布局/横向滚动 */
  .lt-sort-note{margin:6px 2px 14px;color:var(--lt-faint);font-size:10.5px;line-height:1.6}
  .lt-table-wrap{overflow:auto;border:1px solid var(--lt-border);border-radius:13px;background:var(--lt-surface);
    box-shadow:inset 0 1px 0 var(--lt-hairline)}
  .lt-table{width:100%;border-collapse:collapse;font-size:12px;min-width:1200px}
  .lt-table th{position:sticky;top:0;z-index:2;background:var(--lt-th-bg);color:var(--lt-th-text);text-align:left;
    padding:10px 10px;border-bottom:1px solid var(--lt-border);white-space:nowrap;font-size:10px;letter-spacing:.6px;font-weight:700;
    /* 表头一律垂直居中：sticky 下若沿基线对齐，会随列内是否有内联徽章而上下漂移 */
    vertical-align:middle}
  .lt-table th.lt-num{text-align:right}
  .lt-table th.lt-center{text-align:center}
  /* 「竞额20日比%」表头的排序按钮：紧跟列名，默认 ↓（竞价成交额从大到小），点击切 ↑（小到大）。
     只换箭头文字/class，不改列宽口径（跨表列宽仍由 syncTableColumns 统一量算） */
  .lt-sort-btn{margin-left:4px;padding:0 3px;border:0;border-radius:4px;background:transparent;
    color:var(--lt-muted);font:inherit;font-size:11px;line-height:1;cursor:pointer;vertical-align:middle}
  .lt-sort-btn:hover{color:var(--lt-text-2);background:var(--lt-btn-bg-hover)}
  .lt-sort-btn:active{color:var(--lt-text)}
  #dsh-longtou-panel.lt-narrow .lt-sort-btn{margin-left:2px;padding:0 2px;font-size:9px}
  #dsh-longtou-panel.lt-narrow .lt-table{min-width:740px}
  #dsh-longtou-panel.lt-narrow .lt-table th{padding:9px 6px;font-size:9.5px;letter-spacing:.2px}
  /* 窄面板 th 右侧只有 6px padding，箭头缩到 7px 并只抵消 6px，既不挤偏文字也不压到下一列 */
  #dsh-longtou-panel.lt-narrow .lt-tip-mark{font-size:7px;margin-left:1px;margin-right:-6px}
  #dsh-longtou-panel.lt-narrow .lt-table td{padding:8px 6px;font-size:11px}
  .lt-table td{padding:9px 10px;border-bottom:1px solid var(--lt-border-soft);white-space:nowrap;color:var(--lt-text-2);line-height:1.5;
    /* 单元格一律垂直居中：纯文本列（代码/名称）与含 inline-flex 徽章的列不再各对各的基线 */
    vertical-align:middle}
  .lt-table tbody tr:hover td{background:var(--lt-gold-tint-weak)}
  .lt-table tbody tr.lt-row-eligible td{background:var(--lt-row-eligible-bg)}
  .lt-table tbody tr.lt-row-eligible td:first-child{box-shadow:inset 3px 0 0 var(--lt-up-strong)}
  .lt-table tbody tr.lt-row-watch td{background:var(--lt-row-watch-bg)}
  .lt-table tbody tr.lt-row-watch td:first-child{box-shadow:inset 3px 0 0 var(--lt-down-strong)}
  .lt-table tbody tr.lt-row-eligible:hover td{background:var(--lt-row-eligible-bg-hover)}
  .lt-table tbody tr.lt-row-watch:hover td{background:var(--lt-row-watch-bg-hover)}
  .lt-table tbody tr:last-child td{border-bottom:none}
  .lt-code{font-family:ui-monospace,"Cascadia Code",Consolas,monospace;font-size:11px;color:var(--lt-code)}
  .lt-name{font-weight:650;color:var(--lt-strong)}
  .lt-row-eligible .lt-name{color:var(--lt-up-text)}
  .lt-row-watch .lt-name{color:var(--lt-down-text)}
  .lt-highest{color:var(--lt-up-text) !important;background:linear-gradient(90deg,var(--lt-highest-bg-1),var(--lt-highest-bg-2)) !important;
    border-radius:5px;padding:1px 5px;font-weight:800}
  .lt-eligible{color:var(--lt-up-text)}
  .lt-watch{color:var(--lt-down-text)}
  .lt-fold-mark{color:var(--lt-gold);font-size:9px;vertical-align:super;margin-left:1px}
  .lt-emotion-btn{all:unset;display:inline-flex;align-items:center;cursor:pointer;border-radius:999px}
  .lt-emotion-btn:focus-visible{outline:1px solid var(--lt-gold);outline-offset:2px}
  .lt-event-btn{all:unset;display:inline-flex;align-items:center;cursor:pointer;border-radius:6px;color:var(--lt-text-2)}
  .lt-event-btn:focus-visible{outline:1px solid var(--lt-gold);outline-offset:2px}
  .lt-event-head{color:var(--lt-gold);font-weight:750;margin-bottom:5px}
  .lt-event-row{display:flex;gap:6px;align-items:baseline;margin-top:4px;line-height:1.6}
  .lt-event-row b{color:var(--lt-gold);font-weight:700;flex:0 0 auto}
  .lt-event-row span{color:var(--lt-text-2)}
  .lt-emotion-head{color:var(--lt-gold);font-weight:750;margin-bottom:5px}
  .lt-emotion-row{display:flex;align-items:baseline;gap:6px;line-height:1.8}
  .lt-emotion-name{color:var(--lt-text)}
  .lt-emotion-short{color:var(--lt-gold);font-weight:800;letter-spacing:.4px}
  .lt-emotion-group{color:var(--lt-muted);font-size:11px}
  .lt-emotion-text{color:var(--lt-text-2);line-height:1.65}
  .lt-num{font-family:ui-monospace,"Cascadia Code",Consolas,monospace;font-size:11.5px;text-align:right}
  .lt-score{font-family:ui-monospace,"Cascadia Code",Consolas,monospace;font-weight:800;text-align:right}
  .lt-center{text-align:center}
  .lt-text{text-align:left}
  .lt-badge{display:inline-flex;align-items:center;justify-content:center;min-width:24px;padding:2px 8px;
    border-radius:999px;border:1px solid transparent;font-size:11px;font-weight:800;line-height:1.5;white-space:nowrap}
  .lt-grade-A{color:var(--lt-down);background:var(--lt-down-tint-2);border-color:var(--lt-down-tint-3)}
  .lt-grade-B{color:var(--lt-info);background:var(--lt-info-tint-1);border-color:var(--lt-info-tint-2)}
  .lt-grade-C{color:var(--lt-warn);background:var(--lt-warn-tint-1);border-color:var(--lt-warn-tint-2)}
  .lt-grade-D{color:var(--lt-up-soft);background:var(--lt-up-tint-2);border-color:var(--lt-up-tint-3)}
  .lt-action-good{color:var(--lt-up-text);background:var(--lt-up-tint-2);border-color:var(--lt-up-strong)}
  .lt-action-watch,.lt-action-observe{color:var(--lt-down-text);background:var(--lt-row-watch-bg);border-color:var(--lt-down-strong)}
  .lt-action-no{color:var(--lt-up-soft);background:var(--lt-up-dark);border-color:var(--lt-up-deep-bg)}
  .lt-state-good{color:var(--lt-down);background:var(--lt-down-tint-1);border-color:var(--lt-down-tint-2)}
  .lt-state-warn{color:var(--lt-warn);background:var(--lt-warn-tint-1);border-color:var(--lt-warn-tint-2)}
  .lt-state-bad{color:var(--lt-up-soft);background:var(--lt-up-tint-1);border-color:var(--lt-up-tint-3)}
  .lt-state-muted{color:var(--lt-muted);background:var(--lt-muted-tint);border-color:var(--lt-border-soft)}
  .lt-chip-stage{color:var(--lt-gold-soft);background:var(--lt-gold-bg);border:1px solid var(--lt-gold-border);border-radius:6px;
    padding:2px 6px;font-size:10.5px;font-weight:700;letter-spacing:.3px}
  .lt-good{color:var(--lt-down)}
  .lt-warn{color:var(--lt-warn)}
  .lt-bad{color:var(--lt-up-soft)}
  /* 昨日全天成交额 >10 亿：深红文字（不加底色），与涨跌红/绿区分 */
  .lt-amount-hot{color:var(--lt-up-deep);font-weight:800;text-shadow:0 0 6px var(--lt-amount-glow)}
  /* 候选流通市值落在异常端（>90 亿 / <30 亿）：格式与「昨日全天成交额 >10 亿」完全一致（深红 + 加粗 + 微光） */
  .lt-cap-outlier{color:var(--lt-up-deep);font-weight:800;text-shadow:0 0 6px var(--lt-amount-glow)}
  .lt-blue{color:var(--lt-info)}
  .lt-muted{color:var(--lt-muted)}
  .lt-reasons{display:grid;gap:8px}
  .lt-reason{background:linear-gradient(180deg,var(--lt-card-2),var(--lt-surface-2));border:1px solid var(--lt-border);border-left:3px solid var(--lt-btn-border);
    border-radius:11px;padding:11px 13px}
  .lt-reason.good{border-left-color:var(--lt-down)}
  .lt-reason.eligible{border-left-color:var(--lt-up-strong);background:linear-gradient(180deg,var(--lt-up-tint-1),var(--lt-surface-2))}
  .lt-reason.watch{border-left-color:var(--lt-down-strong);background:linear-gradient(180deg,var(--lt-down-tint-1),var(--lt-surface-2))}
  .lt-reason.bad{border-left-color:var(--lt-up-deep-bg)}
  .lt-reason-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px}
  .lt-reason-code{font-family:ui-monospace,"Cascadia Code",Consolas,monospace;font-weight:800;color:var(--lt-strong);font-size:12px}
  .lt-reason-meta{font-size:10.5px;color:var(--lt-muted)}
  .lt-reason p{margin:0;color:var(--lt-text-3);font-size:12px;line-height:1.7}
  .lt-reason p strong{color:var(--lt-strong)}
  .lt-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:9px 20px 11px;
    border-top:1px solid var(--lt-border);background:var(--lt-surface-2);color:var(--lt-faint);font-size:10.5px;flex:0 0 auto}
  .lt-foot .lt-live{display:inline-flex;align-items:center;gap:6px;color:var(--lt-muted)}
  .lt-foot .lt-live::before{content:"";width:6px;height:6px;border-radius:50%;background:var(--lt-down);
    box-shadow:0 0 8px var(--lt-down-glow)}
  .lt-empty{display:grid;place-items:center;gap:8px;min-height:320px;text-align:center;color:var(--lt-muted);font-size:12px;line-height:1.8}
  .lt-empty b{color:var(--lt-gold);font-size:15px}
  .lt-empty code{color:var(--lt-gold-soft);background:var(--lt-gold-bg);border:1px solid var(--lt-gold-border);border-radius:6px;padding:2px 6px}
  .lt-loading{padding:18px 0}
  .lt-loading p{color:var(--lt-muted);font-size:12px;margin:10px 0 0}
  .lt-skeleton{height:12px;border-radius:6px;margin:9px 0;background:linear-gradient(90deg,var(--lt-th-bg),var(--lt-skeleton),var(--lt-th-bg));
    background-size:200% 100%;animation:lt-shimmer 1.2s linear infinite}
  .lt-skeleton:nth-child(2){width:82%}.lt-skeleton:nth-child(3){width:64%}.lt-skeleton:nth-child(4){width:74%}
  @keyframes lt-shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
  .lt-error{color:var(--lt-up-soft);background:var(--lt-up-tint-1);border:1px solid var(--lt-up-tint-3);
    border-radius:10px;padding:12px 14px;font-size:12px;line-height:1.7}
  #dsh-longtou-panel{
    --lt-fs-table:12px;--lt-fs-th:10px;--lt-fs-title:17px;--lt-fs-sub:10px;
    --lt-fs-date:11px;--lt-fs-btn:11px;--lt-fs-h2:12px;--lt-fs-h3:12px;
    --lt-fs-metric:12px;--lt-fs-metric-strong:13px;--lt-fs-num:11.5px;--lt-fs-code:11px;
    --lt-fs-reason:12px;--lt-fs-reason-code:12px;--lt-fs-reason-meta:10.5px;
    --lt-fs-chip:10.5px;--lt-fs-badge:11px;--lt-fs-foot:10.5px;--lt-fs-pos:10.5px;
    --lt-fs-empty:12px;--lt-fs-error:12px;
  }
  #dsh-longtou-panel .lt-table{font-size:var(--lt-fs-table)}
  #dsh-longtou-panel .lt-table th{font-size:var(--lt-fs-th)}
  #dsh-longtou-panel .lt-title{font-size:var(--lt-fs-title)}
  #dsh-longtou-panel .lt-sub{font-size:var(--lt-fs-sub)}
  #dsh-longtou-panel .lt-date{font-size:var(--lt-fs-date)}
  #dsh-longtou-panel .lt-btn{font-size:var(--lt-fs-btn)}
  #dsh-longtou-panel .lt-body h2{font-size:var(--lt-fs-h2)}
  #dsh-longtou-panel .lt-body h3{font-size:var(--lt-fs-h3)}
  #dsh-longtou-panel .lt-metric{font-size:var(--lt-fs-metric)}
  #dsh-longtou-panel .lt-metric strong{font-size:var(--lt-fs-metric-strong)}
  #dsh-longtou-panel .lt-num{font-size:var(--lt-fs-num)}
  #dsh-longtou-panel .lt-code{font-size:var(--lt-fs-code)}
  #dsh-longtou-panel .lt-reason p{font-size:var(--lt-fs-reason)}
  #dsh-longtou-panel .lt-reason-code{font-size:var(--lt-fs-reason-code)}
  #dsh-longtou-panel .lt-reason-meta{font-size:var(--lt-fs-reason-meta)}
  #dsh-longtou-panel .lt-chip-stage{font-size:var(--lt-fs-chip)}
  #dsh-longtou-panel .lt-badge{font-size:var(--lt-fs-badge)}
  #dsh-longtou-panel .lt-foot{font-size:var(--lt-fs-foot)}
  #dsh-longtou-panel .lt-pos{font-size:var(--lt-fs-pos)}
  #dsh-longtou-panel .lt-empty{font-size:var(--lt-fs-empty)}
  #dsh-longtou-panel .lt-error{font-size:var(--lt-fs-error)}
  #dsh-longtou-settings{width:30px;height:30px;border-radius:8px;border:1px solid var(--lt-btn-border);background:var(--lt-th-bg);
    color:var(--lt-text-2);cursor:pointer;font:15px/1 "Segoe UI",sans-serif;margin-left:2px;flex:0 0 auto}
  #dsh-longtou-settings:hover{background:var(--lt-btn-bg-hover);border-color:var(--lt-gold-border-2);color:var(--lt-gold)}
  #dsh-longtou-settings-pop{position:fixed;left:0;top:0;z-index:2147483250;width:238px;display:none;
    background:var(--lt-pop-bg);border:1px solid var(--lt-btn-border);border-radius:12px;padding:13px 14px;color:var(--lt-text-2);
    box-shadow:0 16px 42px var(--lt-shadow);font:12px "Segoe UI","Microsoft YaHei",sans-serif}
  #dsh-longtou-settings-pop.open{display:block}
  /* 日期面板：从「选日期」按钮弹出。必须 fixed 且挂在 body 上，
     否则会被 #dsh-longtou-panel 的 overflow:hidden 裁掉、被面板的拖动/缩放拖走 */
  #dsh-longtou-date-pop{position:fixed;left:0;top:0;z-index:2147483250;display:none;min-width:260px;
    max-height:70vh;overflow:auto;background:var(--lt-pop-bg);border:1px solid var(--lt-btn-border);border-radius:12px;
    padding:12px 13px;color:var(--lt-text-2);box-shadow:0 16px 42px var(--lt-shadow);
    font:12px/1.5 "Segoe UI","Microsoft YaHei",sans-serif}
  #dsh-longtou-date-pop.open{display:block}
  /* 「选日期」面板首行：按年翻页（‹ 上一年 / 2026年 · 区间 / 下一年 ›），居中排在日期格子上方。
     它是面板的 header，和下面的 .lt-date-pop-months（月份列表容器）是兄弟节点 —— 绝不放进列表容器里，
     否则按月分组的遍历会把它当成列表里的一项，把月标题与日期格子挤错位。
     面板自身可滚动，且打开时会自动滚到选中日所在月份，所以这一行 sticky 吸顶、底色与面板同色，
     保证翻页按钮任何时候都在视野内、不挡住下面的日期格子 */
  #dsh-longtou-date-pop .lt-date-pop-year{position:sticky;top:0;z-index:2;display:flex;align-items:center;
    justify-content:center;gap:6px;padding:0 0 8px;margin:0 0 8px;background:var(--lt-pop-bg);
    border-bottom:1px solid var(--lt-border)}
  /* 顶部一行：当前选中的完整年月日 + 总交易日数 */
  #dsh-longtou-date-pop .lt-date-pop-cur{display:flex;justify-content:space-between;align-items:center;gap:10px;
    font-family:ui-monospace,"Cascadia Code",Consolas,monospace;font-size:12px;color:var(--lt-gold);
    margin-bottom:8px;padding-bottom:8px;border-bottom:1px solid var(--lt-border)}
  #dsh-longtou-date-pop .lt-date-pop-cur span{color:var(--lt-muted);font-weight:500;font-size:10px;
    font-family:"Segoe UI","Microsoft YaHei",sans-serif}
  /* 月份表头：同月日期归到一组，7 列网格，行内按升序 */
  #dsh-longtou-date-pop .lt-date-pop-h{color:var(--lt-gold-soft);font-weight:700;font-size:11px;
    letter-spacing:.6px;margin:10px 0 6px}
  #dsh-longtou-date-pop .lt-date-pop-h:first-of-type{margin-top:2px}
  #dsh-longtou-date-pop .lt-days{display:grid;grid-template-columns:repeat(7,1fr);gap:4px}
  #dsh-longtou-date-pop .lt-day{padding:5px 0;border-radius:7px;border:1px solid transparent;background:var(--lt-btn-bg);
    color:var(--lt-text-3);cursor:pointer;text-align:center;
    font:600 11px ui-monospace,"Cascadia Code",Consolas,monospace;transition:background .12s,color .12s,border-color .12s}
  #dsh-longtou-date-pop .lt-day:hover{background:var(--lt-btn-bg-hover);border-color:var(--lt-gold-border);color:var(--lt-gold)}
  #dsh-longtou-date-pop .lt-day.on{background:var(--lt-gold);border-color:var(--lt-gold);color:var(--lt-on-gold);font-weight:800}
  #dsh-longtou-date-pop .lt-day:focus-visible{outline:2px solid var(--lt-gold-border-2);outline-offset:1px}
  #dsh-longtou-settings-pop .lt-set-title{font-weight:750;color:var(--lt-gold);letter-spacing:.5px;margin-bottom:9px;
    display:flex;justify-content:space-between;align-items:center}
  #dsh-longtou-settings-pop .lt-set-title span{color:var(--lt-muted);font-weight:500;font-size:10px}
  #dsh-longtou-settings-pop label{display:flex;justify-content:space-between;align-items:center;
    color:var(--lt-text-3);margin-bottom:6px}
  #dsh-longtou-settings-pop b{color:var(--lt-strong);font-family:ui-monospace,Consolas,monospace;font-size:12px}
  #dsh-longtou-settings-pop input[type=range]{width:100%;accent-color:var(--lt-gold);margin:2px 0 8px}
  #dsh-longtou-settings-pop .lt-set-hint{color:var(--lt-faint);font-size:10.5px;line-height:1.6}
  #dsh-longtou-alpha{width:30px;height:30px;border-radius:8px;border:1px solid var(--lt-btn-border);background:var(--lt-th-bg);
    color:var(--lt-gold);cursor:pointer;font:700 14px/1 Georgia,serif;margin-left:2px;flex:0 0 auto}
  #dsh-longtou-alpha:hover{background:var(--lt-btn-bg-hover);border-color:var(--lt-gold-border-2)}
  #dsh-longtou-market{width:30px;height:30px;border-radius:8px;border:1px solid var(--lt-btn-border);background:var(--lt-th-bg);
    color:var(--lt-gold);cursor:pointer;font:700 13px/1 "Segoe UI","Microsoft YaHei",sans-serif;margin-left:2px;flex:0 0 auto}
  #dsh-longtou-market:hover{background:var(--lt-btn-bg-hover);border-color:var(--lt-gold-border-2)}
  #dsh-longtou-market.lt-on{border-color:var(--lt-gold-border-2);background:var(--lt-btn-bg-hover)}
  #dsh-longtou-notes{width:30px;height:30px;border-radius:8px;border:1px solid var(--lt-btn-border);background:var(--lt-th-bg);
    color:var(--lt-gold);cursor:pointer;font:700 13px/1 "Segoe UI","Microsoft YaHei",sans-serif;margin-left:2px;flex:0 0 auto}
  #dsh-longtou-notes:hover{background:var(--lt-btn-bg-hover);border-color:var(--lt-gold-border-2)}
  #dsh-longtou-notes.lt-on{border-color:var(--lt-gold-border-2);background:var(--lt-btn-bg-hover)}
  /* 「盘前四问 / 封单口径 / 打板纪律」三块默认折叠：内容仍在 DOM 里，只是 display:none，点 📋 展开 */
  #dsh-longtou-panel .lt-note-sec{display:none}
  #dsh-longtou-panel.lt-notes-on .lt-note-sec{display:block}
  #dsh-longtou-alpha-pop{position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);z-index:2147483300;
    width:min(560px,calc(100vw - 24px));max-height:calc(100vh - 40px);overflow:auto;display:none;
    background:var(--lt-pop-bg);border:1px solid var(--lt-btn-border);border-radius:14px;padding:16px 18px;color:var(--lt-text-2);
    box-shadow:0 24px 64px var(--lt-shadow);font:12px "Segoe UI","Microsoft YaHei",sans-serif}
  #dsh-longtou-alpha-pop.open{display:block}
  #dsh-longtou-alpha-pop h3{margin:0 0 10px;color:var(--lt-gold);font-size:15px;display:flex;justify-content:space-between}
  #dsh-longtou-alpha-pop table{width:100%;border-collapse:collapse;margin:6px 0 10px}
  #dsh-longtou-alpha-pop th,#dsh-longtou-alpha-pop td{padding:5px 7px;border-bottom:1px solid var(--lt-border);text-align:left}
  #dsh-longtou-alpha-pop th{color:var(--lt-muted);font-weight:600}
  #dsh-longtou-alpha-pop td:nth-child(2){color:var(--lt-gold);font-weight:750;white-space:nowrap}
  #dsh-longtou-alpha-pop .lt-alpha-note{color:var(--lt-muted);line-height:1.7;margin-top:6px}
  #dsh-longtou-alpha-pop .lt-alpha-note b{color:var(--lt-text)}
  @media (max-width:720px){
    #dsh-longtou-settings-pop{width:220px}
    #dsh-longtou-panel{left:8px;right:8px;bottom:8px;width:auto;height:calc(100vh - 16px);border-radius:14px}
    #dsh-longtou-fab{left:12px;bottom:76px}
    .lt-head{padding:12px 14px;flex-wrap:wrap}
    .lt-nav{padding:6px 12px}
    .lt-body{padding:14px}
    .lt-date{display:none}
    .lt-foot{display:none}
  }
    /* ===== 独立窗口（127.0.0.1:8765）专用：把浮窗还原成整页 ===== */
  /* 浮窗那套 top/left/width/height + 圆角 + 阴影 + 8 个拖拽手柄是给 DSH 页面角落用的，
     直接铺到独立窗口会出现「上面一大片空、面板贴底、正文被切到屏幕外」。 */
  body.dsh-longtou-standalone{overflow:auto}
  body.dsh-longtou-standalone #dsh-longtou-fab{display:none}
  body.dsh-longtou-standalone .lt-resize{display:none}
  /* 全页模式没有浮窗尺寸可切：还原/最小化/置顶按一下会把浮窗几何写回来 → 直接隐藏 */
  body.dsh-longtou-standalone #dsh-longtou-pin,
  body.dsh-longtou-standalone #dsh-longtou-min,
  body.dsh-longtou-standalone #dsh-longtou-max{display:none}
  body.dsh-longtou-standalone .lt-head{padding:10px 16px;gap:10px}
  body.dsh-longtou-standalone .lt-nav{padding:5px 16px}
  body.dsh-longtou-standalone .lt-foot{padding:5px 16px}
  /* 必须 !important：浮窗拖拽/还原会把 left/top/width/height 写成内联样式，
     内联优先级高于类选择器，不加 !important 独立窗口会被压成一条窄条 */
  body.dsh-longtou-standalone #dsh-longtou-panel,
  body.dsh-longtou-standalone #dsh-longtou-panel.open{
    position:static !important;inset:auto !important;
    left:auto !important;top:auto !important;right:auto !important;bottom:auto !important;
    transform:none !important;
    width:100% !important;max-width:100% !important;
    height:100vh !important;max-height:100vh !important;min-width:0 !important;
    margin:0 !important;border:0 !important;border-radius:0 !important;box-shadow:none !important;
  }
  body.dsh-longtou-standalone #dsh-longtou-panel .lt-body{max-height:none !important}
  /* 主题切换按钮（浮窗右上角，太阳/月亮） */
  #dsh-longtou-theme,#dsh-longtou-power,#dsh-longtou-restart{width:30px;padding:6px 0;font-size:15px;line-height:1}
  /* 关机按钮：hover 时转红，和「重启」区分开，避免误点 */
  #dsh-longtou-power:hover{color:var(--lt-gold);border-color:var(--lt-gold)}
  /* ===== 重启动画：面板收起 → 全屏遮罩 → reload 后重新打开 ===== */
  #dsh-longtou-panel{transition:opacity .22s ease,transform .22s ease;transform-origin:center}
  #dsh-longtou-panel.lt-restart-closing{opacity:0;transform:scale(.985);pointer-events:none}
  #dsh-longtou-panel.lt-restart-enter{animation:lt-restart-enter .26s ease both}
  @keyframes lt-restart-enter{from{opacity:0;transform:scale(.99)}to{opacity:1;transform:none}}
  #dsh-longtou-restart-veil{position:fixed;inset:0;z-index:2147483646;display:flex;align-items:center;justify-content:center;
    flex-direction:column;gap:14px;background:var(--lt-bg);color:var(--lt-text);text-align:center;
    font-family:"Segoe UI","Microsoft YaHei",sans-serif;opacity:0;transition:opacity .18s ease}
  #dsh-longtou-restart-veil.lt-veil-in{opacity:1}
  #dsh-longtou-restart-veil .lt-veil-ring{width:36px;height:36px;border-radius:50%;
    border:2px solid var(--lt-gold-border);border-top-color:var(--lt-gold);animation:lt-restart-spin .9s linear infinite}
  @keyframes lt-restart-spin{to{transform:rotate(360deg)}}
  #dsh-longtou-restart-veil .lt-veil-title{font-size:14px;font-weight:700;color:var(--lt-gold);letter-spacing:.5px}
  #dsh-longtou-restart-veil .lt-veil-sub{font-size:11px;color:var(--lt-muted);line-height:1.8;max-width:560px;
    padding:0 24px;word-break:break-all}
  #dsh-longtou-restart-veil .lt-veil-actions{display:flex;gap:10px;margin-top:6px}
  @media (prefers-reduced-motion: reduce){
    #dsh-longtou-panel{transition:none}
    #dsh-longtou-panel.lt-restart-enter{animation:none}
    #dsh-longtou-restart-veil{transition:none}
    #dsh-longtou-restart-veil .lt-veil-ring{animation:none}
  }
  /* ===== 情绪周期区块（内容最上方，数据源 /cycle.json；任一部位缺失就显示占位） ===== */
  .lt-cycle{display:block;border:1px solid var(--lt-border);border-radius:13px;background:var(--lt-surface);
    padding:12px 14px;margin:0 0 18px;box-shadow:inset 0 1px 0 var(--lt-hairline)}
  .lt-cycle-title{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px;
    font-size:var(--lt-fs-h2);font-weight:750;letter-spacing:1.5px;color:var(--lt-gold);text-transform:uppercase}
  .lt-cycle-date{font:500 var(--lt-fs-pos)/1.6 ui-monospace,"Cascadia Code",Consolas,monospace;
    letter-spacing:0;color:var(--lt-muted);text-transform:none}
  .lt-cycle-part{margin-top:10px}
  .lt-cycle-label{font-size:10px;letter-spacing:.6px;color:var(--lt-muted);margin-bottom:6px}
  .lt-cycle-empty{padding:10px 12px;border:1px dashed var(--lt-border);border-radius:8px;background:var(--lt-muted-tint);
    color:var(--lt-muted);font-size:11px;text-align:center}
  .lt-tone-up{color:var(--lt-up)}.lt-tone-down{color:var(--lt-down)}.lt-tone-flat{color:var(--lt-text-2)}
  .lt-cycle-indices{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px}
  .lt-cycle-idx{padding:8px 10px;border:1px solid var(--lt-border);border-radius:10px;background:var(--lt-card)}
  .lt-cycle-idx-name{font-size:11px;color:var(--lt-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .lt-cycle-idx-pct{font:800 16px/1.45 ui-monospace,"Cascadia Code",Consolas,monospace}
  .lt-cycle-idx-point{font:500 11px/1.5 ui-monospace,"Cascadia Code",Consolas,monospace;color:var(--lt-text-3)}
  .lt-cycle-amount{font-size:11.5px;color:var(--lt-text-2);background:var(--lt-surface-2);
    border:1px solid var(--lt-border-soft);border-radius:8px;padding:6px 10px}
  .lt-cycle-amount b{color:var(--lt-strong);font-family:ui-monospace,"Cascadia Code",Consolas,monospace}
  .lt-cycle-amount b.lt-tone-up{color:var(--lt-up)}
  .lt-cycle-amount b.lt-tone-down{color:var(--lt-down)}
  .lt-cycle-temp{display:flex;align-items:baseline;flex-wrap:wrap;gap:8px;font-size:11.5px;color:var(--lt-text-2)}
  .lt-cycle-temp-val{font:800 20px/1.2 ui-monospace,"Cascadia Code",Consolas,monospace;color:var(--lt-temp)}
  .lt-cycle-temp-sep{color:var(--lt-border)}
  .lt-cycle-temp b{color:var(--lt-strong)}
  /* 波浪图 + x 轴标签行 + 连板梯队卡片共用同一个列网格（同一滚动容器），
     列宽/列距/左右留白全部来自 --lt-cv-* 变量，第 i 个标签恒在第 i 张卡片中线上 */
  .lt-cycle-scroll{overflow-x:auto;overflow-y:hidden}
  .lt-cycle-track{width:max-content}
  .lt-cv{display:block}
  /* 最高板纵轴量程可拖动（v1）：绘图区光标提示「可纵向拖拽改变左轴量程」；
     拖动进行中光标由 JS 挂到 body 上（拖出图区也保持 ns-resize），松手复原 */
  .lt-cv{cursor:ns-resize}
  .lt-cv-line{fill:none;stroke:var(--lt-line);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
  .lt-cv-dot{fill:var(--lt-line);stroke:none}
  /* 数值画在同色实心圆内：红圆白字（.lt-cv-val）、黄圆深字（.lt-cv-pressure-num）。
     两种圆半径统一 PRESSURE_NODE_R=8，圆内数字垂直居中 → text y = 点 y + 3.4 */
  .lt-cv-val{fill:#fff;font-size:10px;font-weight:700}
  .lt-cv-val-ex{fill:var(--lt-purple);font-size:10px;font-weight:700}
  .lt-cv-pressure-node{fill:var(--lt-temp);stroke:none}
  /* 孤立单点段的兜底小圆点：与 node 圆同色且更小，实际被 node 圆覆盖，只为开关前后 DOM 一致 */
  .lt-cv-pressure-pt{fill:var(--lt-temp);stroke:none}
  .lt-cv-pressure-num{fill:#111;font-size:10px;font-weight:700}
  .lt-cv-x{fill:var(--lt-muted);font-size:9px}
  .lt-cv-tick{fill:var(--lt-muted);font-size:9px}
  .lt-cv-grid{stroke:var(--lt-grid);stroke-width:1;stroke-dasharray:2 4}
  .lt-cv-bar-up{fill:var(--lt-bar)}
  .lt-cv-bar-down{fill:var(--lt-bar-down)}
  /* 压力板：黄色虚线折线，算法为 C 方案 —— 压力位 P(d) = max(当日最高板 H(d), 前一日最高板 H(d-1))，
     压力位上升当日旧线在 i-1 终止、当日另起新段（断线，旧线不向后延伸），持平/回落则连到当日；
     窗口首日只取当日 H，不并入停牌高度。颜色沿用 --lt-temp 不变。
     压力数字画在同色圆内（.lt-cv-pressure-node + .lt-cv-pressure-num），红圆白字 / 黄圆深字，
     每个点（含突破日、含孤立单点）都有自己的圆和数字 */
  .lt-cv-pressure{fill:none;stroke:var(--lt-temp);stroke-width:var(--lt-cv-pressure-w,1.5);stroke-dasharray:4 2;
    stroke-linejoin:round;stroke-linecap:round}
  .lt-cv-drop{fill:var(--lt-up);font-size:12px;font-weight:800}
  .lt-cycle-legend{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 6px;color:var(--lt-muted);font-size:10px}
  .lt-cycle-legend span{display:inline-flex;align-items:center;gap:4px}
  .lt-cycle-legend i{display:inline-block;width:10px;height:10px;border-radius:2px}
  .lt-lg-dot{background:var(--lt-line);border-radius:50% !important}
  .lt-lg-bar-up{background:var(--lt-bar)}
  .lt-lg-bar-down{background:var(--lt-bar-down)}
  .lt-lg-pressure{background:var(--lt-temp);height:2px !important;width:12px !important;
    border-radius:1px !important}
  .lt-lg-drop{background:transparent;color:var(--lt-up);width:auto !important;height:auto !important;
    font-size:10px;line-height:1}
  /* 当日最高板：紫色虚线，与红线同源 max_height、几何重合，默认不显示，勾选后才叠上去。
     勾选=显示、取消=从 SVG 摘掉这条 polyline；其余线一律不重算 */
  .lt-cv-line-max{fill:none;stroke:var(--lt-purple);stroke-width:2.2;stroke-linejoin:round;
    stroke-linecap:round;stroke-dasharray:5 4}
  .lt-lg-purple{background:var(--lt-purple);border-radius:50% !important}
  .lt-cycle-legend input.lt-cv-max-toggle{width:11px;height:11px;margin:0;accent-color:var(--lt-purple);
    cursor:pointer}
  .lt-cycle-legend input.lt-cv-pressure-toggle{width:11px;height:11px;margin:0;accent-color:var(--lt-temp);
    cursor:pointer}
  /* 区间三档切换（近两年 / 今年 / 去年）：沿用现有的小号淡色胶囊口径（.lt-badge / .lt-chip-stage 的
     字号与圆角），只叠一层边框和底色，不新增尺寸大的控件、不占独立一行；选中档用金色淡底 */
  .lt-range-btn{all:unset;cursor:pointer;border:1px solid var(--lt-border-soft);border-radius:999px;
    padding:1px 7px;font-size:10px;line-height:1.5;color:var(--lt-muted);background:var(--lt-muted-tint)}
  .lt-range-btn:hover{color:var(--lt-text-2);border-color:var(--lt-btn-border-hover)}
  .lt-range-btn:focus-visible{outline:1px solid var(--lt-gold);outline-offset:2px}
  .lt-range-btn.lt-range-on{color:var(--lt-gold-soft);background:var(--lt-gold-bg);border-color:var(--lt-gold-border)}
  .lt-range-btn:disabled{cursor:default;opacity:.45;color:var(--lt-faint);border-color:var(--lt-border-soft)}
  /* 按年翻页条：嵌在「选日期」面板首行（#dsh-longtou-date-pop 内的 .lt-date-pop-year），
     不再占顶部交易日那一行、也不另起一排。按钮沿用 .lt-range-btn 的小胶囊口径，与图例三档同一套视觉；
     禁用态只用现有变量降透明度 */
  .lt-cycle-pager{display:inline-flex;align-items:center;gap:5px;font-size:10px;color:var(--lt-muted)}
  .lt-cycle-pager-label{font-size:10px;letter-spacing:.4px;color:var(--lt-text-2);white-space:nowrap}
  .lt-year-pager{display:inline-flex;align-items:center;gap:5px;min-width:0}
  /* 家数两线（涨停家数 / 跌停家数）：粗线，线宽 = 压力线线宽 × 3 —— 数值由 JS 常量
     CYCLE_COUNT_STROKE 经 svg 上的 --lt-cv-count-w 注入，不在 CSS 里写死具体数字；
     半透明浅色、不抢主折线，线上不带任何数字标签 */
  .lt-cv-count{fill:none;stroke-width:var(--lt-cv-count-w,4.5);stroke-opacity:.22;
    stroke-linejoin:round;stroke-linecap:round}
  .lt-cv-count-up{stroke:var(--lt-up)}
  .lt-cv-count-down{stroke:var(--lt-down)}
  /* 成交额黄线：与上面两条家数线共用同一个 .lt-cv-count 类（同线宽 var(--lt-cv-count-w)、同
     stroke-opacity .22、同实线/round 端点、同样不带圆点），唯一差别是颜色换成黄色 --lt-temp */
  .lt-cv-amount{stroke:var(--lt-temp)}
  /* 两条横贯虚线：高潮预警线（家数轴 100 家）/ 冰点试错线（高度轴 3 板），只画线不写数字 */
  .lt-cv-overheat{fill:none;stroke:var(--lt-warn);stroke-width:1;stroke-dasharray:7 4;stroke-opacity:.75}
  .lt-cv-freeze{fill:none;stroke:var(--lt-info);stroke-width:1;stroke-dasharray:3 4;stroke-opacity:.7}
  /* 悬停竖点划线（点-划交替）：跟随鼠标吸附到最近一列的中心，从 padT 贯穿到 baseY。
     颜色随主题 —— 暗色白 / 亮色黑，由 --lt-cv-cursor 提供；元素由 JS 追加在 SVG 末尾（最上层） */
  .lt-cv-cursor{stroke:var(--lt-cv-cursor,#fff);stroke-width:1;stroke-dasharray:3 3 1 3;fill:none;
    pointer-events:none}
  /* 悬停浮层：显示该日日期 + 涨停/跌停家数，横向跟随鼠标、靠近右边缘自动翻到左侧。
     只读（pointer-events:none），避免鼠标移到浮层上时触发离开事件而闪烁 */
  .lt-cv-hover-tip{position:fixed;z-index:2147483646;display:none;pointer-events:none;max-width:260px;
    padding:5px 8px;border-radius:7px;background:var(--lt-pop-bg);border:1px solid var(--lt-border);
    color:var(--lt-text);font-size:11px;line-height:1.5;white-space:nowrap;
    box-shadow:0 8px 22px var(--lt-shadow-soft)}
  .lt-cv-hover-tip b{color:var(--lt-strong);font-family:ui-monospace,"Cascadia Code",Consolas,monospace}
  .lt-lg-count-up{background:var(--lt-up);opacity:.45;height:3px !important;width:14px !important;
    border-radius:1px !important}
  .lt-lg-count-down{background:var(--lt-down);opacity:.45;height:3px !important;width:14px !important;
    border-radius:1px !important}
  .lt-lg-amount{background:var(--lt-temp);opacity:.45;height:3px !important;width:14px !important;
    border-radius:1px !important}
  .lt-lg-overheat{background:var(--lt-warn);opacity:.8;height:2px !important;width:14px !important;
    border-radius:0 !important}
  .lt-lg-freeze{background:var(--lt-info);opacity:.8;height:2px !important;width:14px !important;
    border-radius:0 !important}
  /* 当日列可点：点击后用现成浮层显示当日涨跌停家数与两条预警线的触发情况（数字只在点击后出现） */
  .lt-cycle-col.lt-cycle-col-click{cursor:pointer}
/* 情绪波浪选中某一天：候选区顶部的状态条 + 那天的梯队表、以及被选中列的描边。
   只在 cycleDayDate 非空时才渲染这些节点，默认态（跟随最新）一个字节都不受影响 */
.lt-day-bar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:6px 8px;margin:0 0 8px;
  border:1px solid var(--lt-line,#c8c8c8);border-radius:6px;font-size:12px}
.lt-day-bar-t{font-weight:600;color:var(--lt-muted)}
.lt-day-bar-hint{color:var(--lt-faint,#999);font-size:11px}
.lt-day-meta{color:var(--lt-muted);font-size:11px;margin:0 0 6px}
.lt-day-table td{vertical-align:top}
.lt-day-boards{white-space:nowrap;color:var(--lt-muted)}
.lt-day-code{margin-left:4px;color:var(--lt-faint,#999);font-style:normal;font-size:11px}
.lt-cycle-col.lt-cycle-col-pick{outline:2px solid var(--lt-line,#c8c8c8);outline-offset:-2px;border-radius:4px}
  /* 梯队竖屏：定高 + 内部上下滚动，列头吸顶不跟着滚（约 6~7 行股票，不再撑高整页）。
     右边距减 12px 抵掉竖向滚动条占的宽度，避免把最右一列裁掉。 */
  .lt-cycle-vp{max-height:250px;overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;
    margin:0 calc(var(--lt-cv-padr) - 12px) 0 var(--lt-cv-padl);padding-bottom:4px}
  .lt-cycle-vp::-webkit-scrollbar{width:8px}
  .lt-cycle-vp::-webkit-scrollbar-track{background:transparent}
  .lt-cycle-vp::-webkit-scrollbar-thumb{background:var(--lt-border);border-radius:4px}
  .lt-cycle-vp::-webkit-scrollbar-thumb:hover{background:var(--lt-muted)}
  .lt-cycle-axis{display:grid;grid-template-columns:repeat(var(--lt-cv-cols),var(--lt-cv-colw));
    column-gap:var(--lt-cv-gap);margin:0;position:sticky;top:0;z-index:2;
    background:var(--lt-surface);padding:1px 0 3px}
  .lt-cycle-axis span{min-width:0;overflow:hidden;white-space:nowrap;text-align:center;
    font-size:9px;line-height:1.4;color:var(--lt-muted)}
  .lt-cycle-ladder{display:grid;grid-template-columns:repeat(var(--lt-cv-cols),var(--lt-cv-colw));
    column-gap:var(--lt-cv-gap);margin:0}
  .lt-cycle-col{min-width:0;padding:7px 8px;
    border:1px solid var(--lt-border-soft);border-radius:9px;background:var(--lt-surface-2)}
  .lt-cycle-col-h{font:800 22px/1.2 ui-monospace,"Cascadia Code",Consolas,monospace;color:var(--lt-text-2);text-align:center}
  /* 列头数字按「当日最高板」的绝对板数取色（与下面各层的列内名次色阶不同源），扫一眼就知道哪列强 */
  .lt-cycle-col-h.lt-lv-t2{color:var(--lt-text-3)}
  .lt-cycle-col-h.lt-lv-t3{color:var(--lt-info)}
  .lt-cycle-col-h.lt-lv-t4{color:var(--lt-violet)}
  .lt-cycle-col-h.lt-lv-t5{color:var(--lt-up)}
  .lt-cycle-col-h.lt-hot{color:var(--lt-up)}
  .lt-cycle-col-d{font-size:9.5px;color:var(--lt-muted);text-align:center;margin-bottom:5px}
  /* 身位层级：板数在上、个股在下，都水平居中；越往下越淡由内联 --lt-fade 控制（最高板恒为 1）。
     另有身位色阶 --lt-lv-tone：按「列内相对名次」取色（第1名红 → 第2名深紫 → 第3名蓝 → 第4名及更后灰），
     红只留给每列最高档，所以不同列的同一板数不一定同色；同一板数的多只股票同色 */
  .lt-cycle-lv{display:block;text-align:center;margin-top:4px;line-height:1.45;opacity:var(--lt-fade,1)}
  .lt-cycle-lv.lt-lv-t2{--lt-lv-tone:var(--lt-text-3)}
  .lt-cycle-lv.lt-lv-t3{--lt-lv-tone:var(--lt-info)}
  .lt-cycle-lv.lt-lv-t4{--lt-lv-tone:var(--lt-violet)}
  .lt-cycle-lv.lt-lv-t5{--lt-lv-tone:var(--lt-up)}
  /* 断板反包叠加层：不是当日真实层级，只在原梯队之后补一行「N+k板」+名称；顶部虚线分隔，亮粉常亮不淡出 */
  .lt-cycle-lv.lt-lv-overlay{--lt-lv-tone:var(--lt-overlay-pink);margin-top:6px;padding-top:4px;
    border-top:1px dashed var(--lt-border-soft)}
  .lt-cycle-lv-b{display:block;font-size:9.5px;color:var(--lt-lv-tone,var(--lt-gold));font-weight:700;letter-spacing:.4px;
    text-align:center;margin-bottom:1px}
  .lt-cycle-stock{display:inline-block;font-size:11px;color:var(--lt-lv-tone,var(--lt-text-2));line-height:1.45;padding:0 2px}
  .lt-cycle-stock.lt-tone-up{color:var(--lt-up);font-weight:750}
  /* 梯队方块里的 09:25 集合竞价成交额：比名字小一号、次要色，占名字下面一行。
     值来自 /cycle.json 的 stock.auction_yi（后端 data/auction_history.json），
     为 null 时整行不渲染（不留空行、不显示 null/NaN） */
  .lt-cycle-stk{display:inline-block;vertical-align:top;text-align:center}
  .lt-cycle-stk .lt-cycle-stock{display:block}
  .lt-cycle-stk-amt{display:block;font-size:9.5px;line-height:1.3;color:var(--lt-muted);
    font-family:ui-monospace,"Cascadia Code",Consolas,monospace;letter-spacing:.2px;opacity:.85}
  /* 竞价量比（= 竞价成交量 ÷ 昨日成交量）：跟在竞价成交额后面，如「0.52亿 / 1.8」。
     三档口径：<0.5 浅灰（弱）、0.5 ≤ x < 2 浅红（偏强）、≥2 亮红加粗（强）；只换 class 不改结构 */
  .lt-auction-rat{color:var(--lt-text-3)}
  .lt-auction-rat.lt-ratio-hot{color:var(--lt-ratio-hot);font-weight:700}
  .lt-auction-rat.lt-ratio-warm{color:var(--lt-ratio-warm)}
  .lt-auction-rat.lt-ratio-cold{color:var(--lt-faint)}
  .lt-cycle-none{font-size:11px;color:var(--lt-faint)}
  @media (max-width:720px){.lt-cycle-indices{grid-template-columns:repeat(auto-fit,minmax(96px,1fr))}}
`;
  document.head.appendChild(css);

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function inline(value) {
    let text = esc(value);
    text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
    text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    return text;
  }
  function highlight(value) {
    const classes = {
      '禁止': 'lt-bad', '不做': 'lt-bad', '死亡换手': 'lt-bad', '不通过': 'lt-bad',
      '降一档': 'lt-bad', '核按钮': 'lt-bad', '断板': 'lt-bad',
      '可研究': 'lt-eligible', '通过': 'lt-good', '健康区': 'lt-good',
      '天量次日': 'lt-good', '不降档': 'lt-good',
      '观察': 'lt-watch', '缩量': 'lt-warn', '灰色区': 'lt-warn', '过热': 'lt-warn',
    };
    return inline(value).replace(
      /(禁止|不做|死亡换手|不通过|降一档|核按钮|断板|可研究|通过|健康区|天量次日|不降档|观察|缩量|灰色区|过热)/g,
      (match) => `<span class="${classes[match]}">${match}</span>`,
    );
  }
  function splitRow(line) {
    return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim());
  }
  // 把「16.10亿」「9500万」「1.2e9」这类文本统一解析成元；解析不出返回 null
  function amountYuan(text) {
    const raw = String(text == null ? '' : text).replace(/[,\s†]/g, '');
    const match = raw.match(/(-?\d+(?:\.\d+)?)\s*(亿|万)?/);
    if (!match) return null;
    const value = Number.parseFloat(match[1]);
    if (!Number.isFinite(value)) return null;
    if (match[2] === '亿') return value * 1e8;
    if (match[2] === '万') return value * 1e4;
    return value;
  }
  function gradeBadge(value) {
    const grade = String(value || '').trim();
    if (!/^[ABCD]$/.test(grade)) return inline(value);
    return `<span class="lt-badge lt-grade-${grade}">${grade}</span>`;
  }
  function actionBadge(value) {
    const text = String(value || '').trim();
    let cls = 'lt-action-watch';
    if (text.includes('可研究')) cls = 'lt-action-good';
    else if (text.includes('禁止') || text.includes('不做')) cls = 'lt-action-no';
    else if (text.includes('观察')) cls = 'lt-action-observe';
    return `<span class="lt-badge ${cls}">${inline(text)}</span>`;
  }
  function stateBadge(value) {
    const text = String(value || '').trim();
    let cls = 'lt-state-muted';
    if (text.includes('健康')) cls = 'lt-state-good';
    else if (text.includes('灰色')) cls = 'lt-state-warn';
    else if (text.includes('死亡')) cls = 'lt-state-bad';
    return `<span class="lt-badge ${cls}">${inline(text)}</span>`;
  }
  function emotionBadge(value) {
    const text = String(value || '').trim();
    let cls = 'lt-state-muted';
    if (text === '独立') cls = 'lt-state-good';
    else if (text.startsWith('助力')) cls = 'lt-state-warn';
    else if (text === '同板') cls = 'lt-state-warn';
    return `<span class="lt-badge ${cls}">${inline(text)}</span>`;
  }
  // 列对齐的唯一来源：表头 th 与数据 td 都调它，避免两处判定漂移导致表头/单元格各对各的
  // 主表 24 列口径：「基础」档已加回（分数之后、最终之前）；「涨幅空间」列已从渲染层删除
  //（当前数据源不再产出该列，历史旧表若仍带这一列同样被下面的 HIDDEN_COLUMNS 跳过）
  const CENTER_COLUMNS = new Set(['阶段', '基础', '最终', '操作', '仓位', '换手状态', '个股情绪', '事件驱动', '昨日全天成交额', '原结论']);
  // 渲染层要隐藏的列名：表头与单元格两处查同一份名单，命中就整列不输出 th / td。
  // 注意：不动 headers 数组本身，gateIndex / auctionIndex / finalIndex 等仍按原始列序取值，口径不变。
  const HIDDEN_COLUMNS = new Set(['涨幅空间']);
  const NUM_COLUMNS = new Set(['分数', '排序', '流通市值(亿)', '龙虎榜净买(亿)']);
  function columnClass(header) {
    const key = String(header == null ? '' : header).trim();
    if (CENTER_COLUMNS.has(key)) return 'lt-center';
    if (NUM_COLUMNS.has(key)) return 'lt-num';
    if (key.endsWith('%')) return 'lt-center'; // 所有含 % 的列居中（昨换手%、竞额20日比%、门槛%、开盘%）
    return 'lt-text'; // 代码、名称、档位等文本列左对齐
  }
  // 「竞额20日比%」列的排序状态 —— 模块级：刷新 / 切换日期重建表格后依然生效。
  //   排序键 = 该列的 20日比百分比（直接来自表格数据，同步可取，首屏即有序）；列内第二行的
  //   「竞价成交额 / 量比」只做展示，不参与排序。
  //   dir=-1 从大到小（默认），dir=1 从小到大。
  const candidateSort = { key: 'ratio', dir: -1 };
  // 取某行的 20日比百分比：去掉 † 折叠标记与百分号；取不到（空 / —）返回 null，排序时统一置底
  function ratioSortValue(cellText) {
    const raw = String(cellText == null ? '' : cellText).replace(/[†%]/g, '').trim();
    const value = Number.parseFloat(raw);
    return Number.isFinite(value) ? value : null;
  }
  // 20日比比较器：缺数据的行不论升/降序都置底；同值返回 0（Array#sort 稳定，保留原顺序）
  function compareRatioCells(textA, textB) {
    const a = ratioSortValue(textA);
    const b = ratioSortValue(textB);
    if (a === null && b === null) return 0;
    if (a === null) return 1;
    if (b === null) return -1;
    return (a - b) * candidateSort.dir;
  }
  function renderTable(block, context = {}) {
    const rows = block.map(splitRow).filter((row) => row.length > 1);
    if (rows.length < 2) return '';
    const headers = rows[0];
    const data = rows.slice(1).filter((row) => !row.every((cell) => /^:?-{3,}:?$/.test(cell)));
    if (!data.length) return '';
    const gateIndex = headers.indexOf('门槛%');
    const codeIndex = headers.indexOf('代码');
    const nameIndex = headers.indexOf('名称');
    const actionIndex = headers.indexOf('操作');
    // 主表已删「操作」列：整行高亮改按「最终」档位判定（A/B 可做、C 观察），
    // 与「最终」单元格同一数据源；「观察区」表没有最终列，回退到旧的 操作 口径
    const finalIndex = headers.indexOf('最终');
    const stageIndex = headers.indexOf('阶段');
    // 观察区表（有「原结论」、没有「最终」）补一列「一句话理由」：
    // 内容取自本报告「## 原因」段的第一条理由，数据源没有就给「—」，不编造。
    if (headers.includes('原结论') && !headers.includes('最终')) {
      headers.push('一句话理由');
      const reasonNotes = context.reasonNotes || {};
      data.forEach((row) => {
        const code = codeIndex >= 0 ? String(row[codeIndex] == null ? '' : row[codeIndex]).trim() : '';
        row.push(reasonNotes[code] || '—');
      });
    }
    // 候选表的默认排序：按模块级 candidateSort 作用在「数据数组」上（不是排 DOM），
    // 所以刷新 / 切换日期 / 轮询重建表格后顺序依旧；排序键是「竞额20日比%」的百分比，
    // 与表格数据同源、同步可取，首屏就是有序的。
    const auctionIndex = headers.indexOf('竞额20日比%');
    if (auctionIndex >= 0 && candidateSort.key === 'ratio' && data.length > 1) {
      data.sort((rowA, rowB) => compareRatioCells(rowA[auctionIndex], rowB[auctionIndex]));
    }
    // 展示列序：把「结构定性」「加分项」两列整体挪到表格最后（两者相对顺序保持不变），
    // 其余列一律维持原始顺序。只影响渲染顺序，不动 headers 本身 —— 上面按原始列序取到的
    // gateIndex / auctionIndex / finalIndex / stageIndex 等下标口径完全不变，单元格仍按
    // 原始 index 取 row[index]；表头 th 与数据 td 都走这同一份 displayIndices，列数天然一致。
    const TAIL_COLUMNS = ['结构定性', '加分项'];
    const displayIndices = (() => {
      const rest = [];
      const tail = [];
      headers.forEach((header, index) => {
        const key = String(header == null ? '' : header).trim();
        (TAIL_COLUMNS.indexOf(key) >= 0 ? tail : rest).push(index);
      });
      // 尾部按 TAIL_COLUMNS 的声明顺序排：结构定性 在前、加分项 在后；表里没有的列自然不占位
      tail.sort((a, b) => TAIL_COLUMNS.indexOf(String(headers[a]).trim())
        - TAIL_COLUMNS.indexOf(String(headers[b]).trim()));
      return rest.concat(tail);
    })();
    const head = displayIndices.map((index) => {
      const header = headers[index];
      // 隐藏列（见 HIDDEN_COLUMNS）：整列不输出 th，与单元格同源判定
      if (HIDDEN_COLUMNS.has(header)) return '';
      // 对齐 class 与单元格同源，逐字相同
      const cls = columnClass(header);
      if (header === '昨日全天成交额') {
        return `<th class="${cls}"><span class="lt-tip" data-tip="昨日（T-1）全天成交额。≥1 亿显示为亿（两位小数），否则显示为万（一位小数）。">昨日全天成交额<span class="lt-tip-mark">▾</span></span></th>`;
      }
      if (header === '竞额20日比%') {
        // 列名右侧的排序箭头：↓ = 竞价成交额从大到小（默认），↑ = 从小到大；点击由 bindTooltips 统一绑定
        const mark = candidateSort.dir < 0 ? '↓' : '↑';
        const order = candidateSort.dir < 0 ? '从大到小' : '从小到大';
        return `<th class="${cls}"><span class="lt-tip" data-tip="竞价 9:25 结束时的成交额 ÷ 最近 20 个交易日最大单日成交额（地天板/水下日按 50% 折算）× 100%。用于判断竞价承接强弱。">竞额20日比%<span class="lt-tip-mark">▾</span></span><button type="button" class="lt-sort-btn" data-sort="ratio" title="按竞额20日比%排序（当前${order}，点击切换）" aria-label="按竞额20日比%排序，当前${order}">${mark}</button></th>`;
      }
      if (header === '昨换手%') {
        return `<th class="${cls}"><span class="lt-tip" data-tip="前一交易日全天成交量 ÷ 流通股本 × 100%。不是今日竞价换手，也不是今日实时换手。">昨换手%<span class="lt-tip-mark">▾</span></span></th>`;
      }
      if (header === '个股情绪') {
        return `<th class="${cls}"><span class="lt-tip" data-tip="同板块/同概念内只有该股连板=独立；有低位1-3板连板=助力N，数量越多越强；占评分8.57%。">个股情绪<span class="lt-tip-mark">▾</span></span></th>`;
      }
      if (header === '事件驱动') {
        return `<th class="${cls}" title="新闻（信息/政策/题材舆情）、大盘（市场风险）、热点（主线/新题材/发酵/退潮）；不计分">事件驱动</th>`;
      }
      if (header === '开盘%') {
        return `<th class="${cls}" title="合理区间 -1%~+1%；+1%~+4% 加一档；<-1% 或 >4% 降一档">开盘%</th>`;
      }
      return `<th class="${cls}">${inline(header)}</th>`;
    }).join('');
    const body = data.map((row) => {
      // 整行高亮：优先按「最终」档位（A/B → 可做，C → 观察），无该列时回退到「操作」文本
      let rowClass = '';
      if (finalIndex >= 0) {
        const grade = String(row[finalIndex] || '').trim();
        rowClass = grade === 'A' || grade === 'B'
          ? 'lt-row-eligible'
          : grade === 'C'
            ? 'lt-row-watch'
            : '';
      } else {
        const action = actionIndex >= 0 ? String(row[actionIndex] || '') : '';
        rowClass = action.includes('可研究')
          ? 'lt-row-eligible'
          : action.includes('观察')
            ? 'lt-row-watch'
            : '';
      }
      const stage = stageIndex >= 0 ? String(row[stageIndex] || '') : '';
      const stageMatch = stage.match(/(\d+)/);
      const stageHeight = stageMatch ? Number.parseInt(stageMatch[1], 10) : 0;
      const cells = displayIndices.map((index) => {
        const header = headers[index];
        // 隐藏列：整列不输出 td（与表头同一份 HIDDEN_COLUMNS），行数据 index 不变
        if (HIDDEN_COLUMNS.has(header)) return '';
        const text = String(row[index] == null ? '' : row[index]);
        let html = inline(text);
        let cls = '';
        const isHighest = header === '代码' || header === '名称'
          ? (context.codes || []).includes(text) || (context.height > 0 && stageHeight === context.height)
          : false;
        // 单元格只挂对齐 class；颜色/字体/徽章等修饰一律放到内层 span，保证 th.class 与 td.class 逐字相同
        if (header === '阶段') {
          html = `<span class="lt-chip-stage">${html}</span>`;
        } else if (header === '代码') {
          if (isHighest) html = `<span class="lt-highest">${html}</span>`;
          html = `<span class="lt-code">${html}</span>`;
        } else if (header === '名称') {
          if (isHighest) html = `<span class="lt-highest">${html}</span>`;
          html = `<span class="lt-name">${html}</span>`;
        } else if (header === '分数') {
          const score = Number.parseFloat(text);
          const tone = Number.isFinite(score)
            ? score >= 80 ? ' lt-good' : score >= 65 ? ' lt-blue' : score >= 50 ? ' lt-warn' : ' lt-bad'
            : '';
          html = `<span class="lt-score${tone}">${html}</span>`;
        } else if (header === '最终' || header === '基础') {
          // 「基础」与「最终」同为 A/B/C/D 档位徽章，列由表头派生，无需显式数组
          html = gradeBadge(text);
        } else if (header === '操作') {
          html = actionBadge(text);
        } else if (header === '仓位') {
          const pct = Number.parseFloat(text);
          const tone = !Number.isFinite(pct) || pct <= 0 ? 'lt-state-muted' : pct >= 50 ? 'lt-state-good' : 'lt-state-warn';
          html = `<span class="lt-badge ${tone}">${html}</span>`;
        } else if (header === '换手状态') {
          html = stateBadge(text);
        } else if (String(header == null ? '' : header).trim().startsWith('流通市值')) {
          // 候选流通市值列：>90 亿 或 <30 亿 标红（阈值见文件顶部 CAP_RED_HIGH_YI / CAP_RED_LOW_YI）；
          // 落在 [30, 90] 区间内不标，空值 / '--' / 非数字不标；只加内层 span，不改格式化与对齐
          if (isCapOutlierYi(text)) html = `<span class="lt-cap-outlier">${html}</span>`;
        } else if (header === '昨日全天成交额') {
          // 金额 > 10 亿（1e9 元）整格文字深红；对齐仍由列的 lt-center 决定
          const amount = amountYuan(text);
          if (amount !== null && amount > 1e9) html = `<span class="lt-amount-hot">${html}</span>`;
        } else if (header === '事件驱动') {
          const code = codeIndex >= 0 ? String(row[codeIndex] || '').trim() : '';
          const info = (context.eventDetails || {})[code];
          html = `<span class="lt-event-btn">${html}</span>`;
          if (info && typeof info === 'object') {
            html = `<button type="button" class="lt-event-btn" data-tip="${esc(info.detail || '')}" data-event="${esc(JSON.stringify(info))}" aria-label="查看事件驱动详情">${html}</button>`;
          }
        } else if (header === '个股情绪') {
          const code = codeIndex >= 0 ? String(row[codeIndex] || '').trim() : '';
          const detail = (context.emotionDetails || {})[code];
          html = emotionBadge(text);
          if (detail) {
            if (typeof detail === 'object') {
              html = `<button type="button" class="lt-emotion-btn" data-tip="${esc(detail.text || '')}" data-emotion="${esc(JSON.stringify(detail))}" aria-label="查看个股情绪明细">${html}</button>`;
            } else {
              html = `<button type="button" class="lt-emotion-btn" data-tip="${esc(detail)}" aria-label="查看个股情绪明细">${html}</button>`;
            }
          }
        } else if (header === '竞额20日比%') {
          const ratio = Number.parseFloat(text);
          const gate = gateIndex >= 0 ? Number.parseFloat(row[gateIndex]) : Number.NaN;
          const pass = Number.isFinite(ratio) && Number.isFinite(gate) && ratio >= gate;
          if (text.includes('†')) {
            html = html.replace('†', '<span class="lt-fold-mark">†</span>');
          }
          html = `<span class="${pass ? 'lt-good' : 'lt-bad'}">${html}</span>`;
          // 竞价成交额（亿/万自适应单位，见 formatAuctionAmount）：先留占位，由 fillAuctionAmounts 异步填 /auction.json 的值；
          // 取不到就保持空白，绝不显示 null / NaN。
          const auctionCode = codeIndex >= 0 ? String(row[codeIndex] || '').trim() : '';
          if (auctionCode) {
            html += `<span class="lt-auction-amt" data-auction-code="${esc(auctionCode)}" style="display:block;font-size:11px;line-height:1.4;opacity:.7"></span>`;
          }
        } else if (header === '开盘%') {
          const gap = Number.parseFloat(text);
          const tone = gap >= -1 && gap <= 4 ? 'lt-good' : 'lt-bad';
          html = `<span class="${tone}">${html}</span>`;
        } else if (header === '门槛%') {
          html = `<span class="lt-muted">${html}</span>`;
        }
        return `<td class="${columnClass(header)}">${html}</td>`;
      }).join('');
      return `<tr class="${rowClass}">${cells}</tr>`;
    }).join('');
    // 排序口径脚注：只挂在 24 列阶段表（含「最终」+「分数」）下方，其他表不加。
    // 口径与 longtou/cli.py 的 _score_key 一致：阶段内分数降序；无分数行置底、错误行再置底，
    // 同分保持报告原始顺序（Python sorted 稳定）。这里不写「最终档/封流比」那种并不存在的排序键。
    const sortNote = headers.includes('最终') && headers.includes('分数')
      ? '<div class="lt-sort-note">排序口径：默认按「竞额20日比%」从大到小，点该列表头箭头可切升/降序；该列缺数据的行始终置底。报告原始顺序为阶段内按「分数」降序、同分保持原序。</div>'
      : '';
    return `<div class="lt-table-wrap"><table class="lt-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>${sortNote}`;
  }
  // 指标「可接受区间」：纯经验值，不是回测统计，按需调整这里的数字即可。
  // 键名必须与 markdown 顶部「## 市场」里的指标名逐字一致；数据源里没有的指标（量比、晋级率、
  // 情绪分、历史统计区间）一律不在此表、也不显示区间，避免把编造值当成统计结论。
  // min/max 含端点；unit 只用于展示，缺省表示纯数值。
  const METRIC_RANGES = {
    '封板率': { min: 55, max: 85, unit: '%' },   // 涨停/(涨停+炸板)，低于 55% 说明封板承接弱
    '炸板': { min: 0, max: 40, unit: '家' },      // 炸板家数，过热时后续接力风险抬升
    '最高板': { min: 3, max: 8, unit: '板' },     // 连板高度=空间高度
    '最高板一字占比': { min: 0, max: 80, unit: '%' }, // 一字过头意味着缺乏换手承接
    // 「跌停强度」是报告自带的复合口径（不是家数、也不是百分比），历史分布未核实，
    // 一旦上限设错就会天天标「当前超出」，标红随即失去信号意义 —— 故不纳入区间判定。
    '跌停': { min: 0, max: 10, unit: '家' },
    '高位跌停': { min: 0, max: 2, unit: '家' },
    '一字跌停': { min: 0, max: 2, unit: '家' },
  };
  // 从一条指标卡文本里抽出「指标名 + 数值」，数值形如 **61.4%** / **5** / **5.3**
  // 键名可能互为子串（「跌停」是「高位跌停」「一字跌停」的后缀），若按声明顺序匹配，
  // 「高位跌停 **2**」会同时命中「高位跌停」和「跌停」两个键，卡面就会多出一条不相干的区间。
  // 因此按键长降序匹配，并记录已被长键占用的文本区间，落在其中的短键命中一律视为子串误命中。
  function metricRangeNotes(item) {
    const text = String(item == null ? '' : item);
    const notes = [];
    const taken = [];
    Object.keys(METRIC_RANGES)
      .sort((a, b) => b.length - a.length)
      .forEach((key) => {
        const range = METRIC_RANGES[key];
        const match = text.match(new RegExp(`${key}\\s*\\*\\*\\s*(-?\\d+(?:\\.\\d+)?)`));
        if (!match || typeof match.index !== 'number') return;
        const start = match.index;
        const end = start + key.length;
        if (taken.some(([s, e]) => start >= s && end <= e)) return;
        const value = Number.parseFloat(match[1]);
        if (!Number.isFinite(value)) return;
        taken.push([start, end]);
        const ok = value >= range.min && value <= range.max;
        notes.push({
          ok,
          text: `${key} 可接受 ${range.min}~${range.max}${range.unit || ''}${ok ? '' : '（当前超出）'}`,
        });
      });
    return notes;
  }
  function renderMetrics(items) {
    if (!items.length) return '';
    return `<div class="lt-metrics">${items.map((item) => {
      // 最高板沿用既有红色强调；lt-out 表示本卡有指标超出经验区间（两者可能同时成立）
      const top = /最高板/.test(item) ? ' lt-top' : '';
      const notes = metricRangeNotes(item);
      const out = notes.some((note) => !note.ok) ? ' lt-out' : '';
      const hint = notes.length
        ? `<div class="lt-range${out ? ' lt-range-out' : ''}">${notes.map((note) => esc(note.text)).join(' · ')}</div>`
        : '';
      return `<div class="lt-metric${top}${out}">${inline(item)}${hint}</div>`;
    }).join('')}</div>`;
  }
  // 是否整段渲染报告末尾的「## 原因」区块。需求方要求面板隐藏这一栏，
  // 逐股理由改由观察区的「一句话理由」列承载（来源同样是「## 原因」，见 extractReasonNotes）。
  // 想恢复整段：把下面这行改成 true。
  const SHOW_REASON_SECTION = false;
  // 「## 市场」段默认隐藏，右上角市场键可切换
  let SHOW_MARKET_SECTION = false;
  // 三块说明性正文（盘前四问 / 封单口径 / 打板纪律）：默认折叠，α 右侧 📋 键切换
  const NOTE_SECTION_HEADS = /^(盘前四问|封单口径|打板纪律)$/;

  function renderReasons(items) {
    if (!items.length) return '';
    const cards = items.map((item) => {
      const match = item.match(/^\*\*(.+?)\*\*\s*(\([^)]*\))?\s*[:：]\s*(.*)$/);
      const code = match ? match[1] : '';
      const meta = match && match[2] ? match[2] : '';
      const body = match ? match[3] : item;
      let tone = 'watch';
      if (/可研究/.test(body)) tone = 'eligible';
      else if (/观察/.test(body)) tone = 'watch';
      else if (/禁止|不做|死亡换手|不通过|核按钮|断板/.test(body)) tone = 'bad';
      else if (/通过|健康区|天量次日|不降档/.test(body)) tone = 'good';
      return `<div class="lt-reason ${tone}">
        ${code ? `<div class="lt-reason-head"><span class="lt-reason-code">${inline(code)}</span><span class="lt-reason-meta">${inline(meta)}</span></div>` : ''}
        <p>${highlight(body)}</p>
      </div>`;
    }).join('');
    return `<div class="lt-reasons">${cards}</div>`;
  }
  function extractTop(markdown) {
    const match = String(markdown || '').match(/最高板\s*\*\*(\d+)\*\*\s*\(([^)]*)\)/);
    if (!match) return { height: 0, codes: [] };
    return {
      height: Number.parseInt(match[1], 10) || 0,
      codes: match[2].match(/\d{6}/g) || [],
    };
  }
  // 观察区「一句话理由」：报告「## 原因」段本来就对每只给了逐条理由，
  // 这里只截取第一条（按 ；/。/; 切分）填进观察区最后一列，不新增字段、不编造内容；
  // 取不到理由的代码在表里显示「—」。
  function extractReasonNotes(markdown) {
    const notes = {};
    const parts = String(markdown || '').split(/^##\s*原因\s*$/m);
    const section = parts.length > 1 ? parts.slice(1).join('\n## 原因\n') : '';
    const pattern = /^-\s*\*\*(\d{6})\s+[^*]*\*\*\s*(?:\([^)]*\))?\s*[:：]\s*(.+)$/gm;
    let match;
    while ((match = pattern.exec(section))) {
      const first = String(match[2] || '').split(/[；;。]/)[0].trim();
      if (!first) continue;
      notes[match[1]] = first.length > 42 ? `${first.slice(0, 42)}…` : first;
    }
    return notes;
  }
  function emotionTooltipHtml(info) {
    const label = esc(info.label || '');
    const items = Array.isArray(info.items) ? info.items : [];
    const group = items.length ? items[0].group || '' : '';
    const head = `<div class="lt-emotion-head">${label}${group ? ` · ${esc(group)}` : ''}</div>`;
    if (!items.length) {
      return `${head}<div class="lt-emotion-text">${esc(info.text || '')}</div>`;
    }
    const rows = items.map((item) => `<div class="lt-emotion-row"><span class="lt-emotion-name">${esc(item.name || '')}</span><span class="lt-emotion-short">${esc(item.label || item.short || '')}</span><span class="lt-emotion-group">${esc(item.group || '')}</span></div>`).join('');
    return `${head}${rows}`;
  }

  function eventTooltipHtml(info) {
    return `<div class="lt-event-head">事件驱动 · ${esc(info.label || '')}</div>
      <div class="lt-event-row"><b>新闻</b><span>${esc(info.news || '')}</span></div>
      <div class="lt-event-row"><b>大盘</b><span>${esc(info.market || '')}</span></div>
      <div class="lt-event-row"><b>热点</b><span>${esc(info.hotspot || '')}</span></div>`;
  }

  function extractEventDetails(markdown) {
    const details = {};
    const clean = String(markdown || '').replace(/<!--\s*event:(\d{6})\s+([^\n]*?)-->/g, (_, code, detail) => {
      const raw = String(detail || '').trim();
      try {
        details[code] = JSON.parse(raw);
      } catch {
        const fallback = raw.match(/"detail"\s*:\s*"([^"]*)"/);
        details[code] = fallback ? fallback[1] : '';
      }
      return '';
    });
    return { markdown: clean, details };
  }

  function extractEmotionDetails(markdown) {
    const details = {};
    const clean = String(markdown || '').replace(/<!--\s*emotion:(\d{6})\s+([^\n]*?)-->/g, (_, code, detail) => {
      const raw = String(detail || '').trim();
      try {
        details[code] = JSON.parse(raw);
      } catch {
        // Never dump the raw machine payload into a tooltip.
        const fallback = raw.match(/"text"\s*:\s*"([^"]*)"/);
        details[code] = fallback ? fallback[1] : '';
      }
      return '';
    });
    return { markdown: clean, details };
  }

  // 候选区各阶段块按板数「由高到低」排列（4→5 → 3→4 → 2→3）；报告里缺的阶段补一个
  // 「本阶段无候选」占位，保证 2→3 以上的进阶（3→4、4→5）每次都在候选区完整列出。
  // 只重排「## 候选」段内以 ### 开头的块，其它章节与正文顺序一律不动；占位不含任何股票。
  const CANDIDATE_STAGE_TITLES = ['4→5', '3→4', '2→3'];
  function stageBoardOf(text) {
    const match = String(text == null ? '' : text).match(/^(\d+)\s*→\s*(\d+)/);
    return match ? Number(match[1]) : null;
  }
  function reorderCandidateStages(lines) {
    const list = Array.isArray(lines) ? lines.slice() : [];
    const start = list.findIndex((line) => /^##\s/.test(line) && /候选/.test(line));
    if (start < 0) return list;
    let end = list.length;
    for (let i = start + 1; i < list.length; i += 1) {
      if (/^##\s/.test(list[i])) { end = i; break; }
    }
    const body = list.slice(start + 1, end);
    const preamble = [];
    const blocks = [];
    body.forEach((line) => {
      if (/^###\s/.test(line)) {
        const title = line.replace(/^###\s+/, '').trim();
        blocks.push({ title, lines: [line] });
      } else if (blocks.length) {
        blocks[blocks.length - 1].lines.push(line);
      } else {
        preamble.push(line);
      }
    });
    if (!blocks.length) return list;
    CANDIDATE_STAGE_TITLES.forEach((title) => {
      if (!blocks.some((block) => block.title === title)) {
        blocks.push({ title, lines: ['### ' + title, '', '本阶段无候选', ''] });
      }
    });
    // 稳定降序：板数高的在前；解析不出板数的块保持原相对位置排在最后
    const staged = blocks.filter((block) => stageBoardOf(block.title) != null);
    const others = blocks.filter((block) => stageBoardOf(block.title) == null);
    staged.sort((left, right) => stageBoardOf(right.title) - stageBoardOf(left.title));
    const ordered = [];
    preamble.forEach((line) => ordered.push(line));
    staged.forEach((block) => block.lines.forEach((line) => ordered.push(line)));
    others.forEach((block) => block.lines.forEach((line) => ordered.push(line)));
    return list.slice(0, start + 1).concat(ordered, list.slice(end));
  }
  function renderMarkdown(markdown, emotionDetails = {}, eventDetails = {}) {
    const lines = reorderCandidateStages(String(markdown || '').split(/\r?\n/));
    const top = extractTop(markdown);
    const reasonNotes = extractReasonNotes(markdown); // 观察区「一句话理由」的唯一来源
    const out = [];
    let mode = '';
    let hiddenMode = false; // 当前 ## 段是否整体隐藏
    // 「## 盘前四问 / ## 封单口径 / ## 打板纪律」三块用 .lt-note-sec 包起来，默认折叠（α 右侧 📋 切换），
    // 文字与结构保持原样，只是外层加一个可隐藏容器。
    let noteOpen = false;
    const closeNote = () => { if (noteOpen) { out.push('</div>'); noteOpen = false; } };
    let index = 0;
    while (index < lines.length) {
      const line = lines[index];
      if (!line.trim()) { index += 1; continue; }
      if (line.trim().startsWith('<!--')) { index += 1; continue; } // machine-readable hidden detail
      if (line.startsWith('# ')) { index += 1; continue; } // report title is in the header
      if (line.startsWith('### ')) {
        closeNote();
        mode = line.slice(4).trim();
        hiddenMode = false;
        out.push(`<h3>${inline(mode)}</h3>`);
        index += 1;
        continue;
      }
      if (line.startsWith('## ')) {
        closeNote();
        mode = line.slice(3).trim();
        // 「## 原因」整段按需求隐藏：标题与下面的条目都不进 DOM
        if (!SHOW_REASON_SECTION && /^原因$/.test(mode)) { index += 1; continue; }
        // 「## 市场」默认隐藏：标题与下面的卡片都不进 DOM，直到下一个标题
        hiddenMode = !SHOW_MARKET_SECTION && /^市场/.test(mode);
        if (hiddenMode) { index += 1; continue; }
        // 三块说明性正文：标题与后续内容统一收进折叠容器
        if (NOTE_SECTION_HEADS.test(mode)) {
          out.push('<div class="lt-note-sec">');
          out.push(`<h2>${inline(mode)}</h2>`);
          noteOpen = true;
          index += 1;
          continue;
        }
        out.push(`<h2>${inline(mode)}</h2>`);
        index += 1;
        continue;
      }
      if (hiddenMode) { index += 1; continue; } // 隐藏段里的表格/列表/段落一律跳过
      if (line.trim().startsWith('|')) {
        const block = [];
        while (index < lines.length && lines[index].trim().startsWith('|')) {
          block.push(lines[index]);
          index += 1;
        }
        out.push(renderTable(block, { ...top, emotionDetails, eventDetails, reasonNotes }));
        continue;
      }
      if (/^- /.test(line)) {
        const items = [];
        while (index < lines.length && /^- /.test(lines[index])) {
          items.push(lines[index].slice(2));
          index += 1;
        }
        if (mode.includes('市场')) out.push(renderMetrics(items));
        else if (mode.includes('原因')) { if (SHOW_REASON_SECTION) out.push(renderReasons(items)); }
        else out.push(`<ul>${items.map((item) => `<li>${inline(item)}</li>`).join('')}</ul>`);
        continue;
      }
      const paragraph = [];
      while (
        index < lines.length &&
        lines[index].trim() &&
        !lines[index].startsWith('#') &&
        !lines[index].trim().startsWith('|') &&
        !/^- /.test(lines[index])
      ) {
        paragraph.push(lines[index]);
        index += 1;
      }
      out.push(`<p>${inline(paragraph.join(' '))}</p>`);
    }
    closeNote(); // 文件末尾仍在折叠段里时补上收尾标签
    return out.join('\n');
  }
  function extractDate(markdown) {
    const text = String(markdown || '');
    const trade = text.match(/交易日\s*(\d{4}-\d{2}-\d{2})/);
    if (trade) return trade[1];
    const match = text.match(/^# .*?—\s*(\d{4}-\d{2}-\d{2})/m);
    return match ? match[1] : '';
  }
  function parseHistory(markdown) {
    const text = String(markdown || '');
    const pattern = /<!-- report:(\d{8}) -->([\s\S]*?)<!-- \/report -->/g;
    const items = [];
    let match;
    while ((match = pattern.exec(text))) {
      items.push({ date: match[1], markdown: match[2].trim() });
    }
    if (!items.length) {
      const date = extractDate(text).replace(/-/g, '');
      if (text.trim()) items.push({ date, markdown: text.trim() });
    }
    items.sort((left, right) => left.date.localeCompare(right.date));
    return items;
  }
  function countCandidates(markdown) {
    return (String(markdown || '').match(/^\|\s*[2-4]→/gm) || []).length;
  }

  const panel = document.createElement('section');
  panel.id = 'dsh-longtou-panel';
  panel.innerHTML = `
    <div class="lt-head">
      <div class="lt-brand">
        <div class="lt-brand-mark">龙</div>
        <div><div class="lt-title">龙头候选</div><div class="lt-sub" id="dsh-longtou-sub">4→5 起，随天梯自动扩展</div></div>
      </div>
      <div class="lt-head-actions">
        <span class="lt-date" id="dsh-longtou-date">—</span>
        <button class="lt-btn" id="dsh-longtou-pin" type="button" title="窗口置顶">置顶</button>
        <button class="lt-btn" id="dsh-longtou-copy" type="button">复制</button>
        <button class="lt-btn" id="dsh-longtou-refresh" type="button">刷新</button>
        <button class="lt-btn lt-icon" id="dsh-longtou-theme" type="button" title="切换亮色" aria-label="切换亮色">☀</button>
        <button class="lt-btn lt-icon" id="dsh-longtou-restart" type="button" title="重启面板服务" aria-label="重启">⟳</button>
        <button class="lt-btn lt-icon" id="dsh-longtou-min" type="button" title="最小化" aria-label="最小化">—</button>
        <button class="lt-btn lt-icon" id="dsh-longtou-max" type="button" title="放大" aria-label="放大">□</button>
        <button class="lt-btn lt-icon" id="dsh-longtou-power" type="button" title="关机（只关面板窗口，服务继续运行）" aria-label="关机">⏻</button>
        <button class="lt-btn lt-icon" id="dsh-longtou-close" type="button" aria-label="关闭" hidden style="display:none">×</button>
      </div>
    </div>
    <div class="lt-nav">
      <button class="lt-btn" id="dsh-longtou-prev" type="button">‹ 上一日</button>
      <span class="lt-pos" id="dsh-longtou-pos">—</span>
      <button class="lt-btn" id="dsh-longtou-next" type="button">下一日 ›</button>
      <span class="lt-spacer"></span>
      <span class="lt-auto" id="dsh-longtou-auto" title="自动刷新时间表加载中…">自动刷新 —</span>
      <button class="lt-btn" id="dsh-longtou-pick" type="button" aria-expanded="false" title="按年-月-日精确选交易日">选日期</button>
      <button class="lt-btn" id="dsh-longtou-latest" type="button">最新</button>
    </div>
    <div class="lt-resize lt-resize-n" data-dir="n"></div>
    <div class="lt-resize lt-resize-e" data-dir="e"></div>
    <div class="lt-resize lt-resize-s" data-dir="s"></div>
    <div class="lt-resize lt-resize-w" data-dir="w"></div>
    <div class="lt-resize lt-resize-nw" data-dir="nw"></div>
    <div class="lt-resize lt-resize-ne" data-dir="ne"></div>
    <div class="lt-resize lt-resize-sw" data-dir="sw"></div>
    <div class="lt-resize lt-grip" data-dir="se" title="拖动拉伸大小"></div>
    <div class="lt-body" id="dsh-longtou-body">
      <div class="lt-empty"><b>龙头候选</b><span>在对话里运行 <code>longtou_report(date="20260828")</code>，生成后点刷新。</span></div>
    </div>
    <div class="lt-foot"><span class="lt-live">09:25 信号 · 9:30 成交 · 一字只观察</span><span id="dsh-longtou-status">等待报告</span></div>
  `;

  const fab = document.createElement('button');
  fab.id = 'dsh-longtou-fab';
  fab.type = 'button';
  fab.title = '龙头候选';
  fab.setAttribute('aria-label', '打开龙头候选');
  fab.textContent = '龙';

  const mount = document.body || document.documentElement;
  const tipEl = document.createElement('div');
  tipEl.id = 'dsh-longtou-tip';

  const settingsBtn = document.createElement('button');
  settingsBtn.id = 'dsh-longtou-settings';
  settingsBtn.type = 'button';
  settingsBtn.title = '设置';
  settingsBtn.setAttribute('aria-label', '打开设置');
  settingsBtn.textContent = '⚙';

  const settingsPop = document.createElement('div');
  settingsPop.id = 'dsh-longtou-settings-pop';
  settingsPop.innerHTML = `
    <div class="lt-set-title">设置 <span>面板</span></div>
    <label for="dsh-longtou-font-range">字体大小 <b id="dsh-longtou-font-value">12px</b></label>
    <input id="dsh-longtou-font-range" type="range" min="8" max="24" step="1" value="12">
    <div class="lt-set-hint">8–24px；只影响面板文字，自动保存。</div>
  `;

  const alphaBtn = document.createElement('button');
  alphaBtn.id = 'dsh-longtou-alpha';
  alphaBtn.type = 'button';
  alphaBtn.title = '阿尔法构成';
  alphaBtn.setAttribute('aria-label', '查看阿尔法构成');
  alphaBtn.textContent = 'α';

  // 市场指标显隐开关：紧挨「阿尔法构成」右侧，默认隐藏
  const marketBtn = document.createElement('button');
  marketBtn.id = 'dsh-longtou-market';
  marketBtn.type = 'button';
  marketBtn.textContent = '📈';
  marketBtn.setAttribute('aria-label', '显示或隐藏市场指标');
  marketBtn.setAttribute('aria-pressed', 'false');
  marketBtn.title = '市场指标（点击显示）';

  // 盘前四问 / 封单口径 / 打板纪律 三块的折叠开关：插在 α 右侧、市场键左边，默认收起
  const notesBtn = document.createElement('button');
  notesBtn.id = 'dsh-longtou-notes';
  notesBtn.type = 'button';
  notesBtn.textContent = '📋';
  notesBtn.setAttribute('aria-label', '盘前四问与纪律');
  notesBtn.setAttribute('aria-pressed', 'false');
  notesBtn.title = '盘前四问与纪律（点击显示）';

  const alphaPop = document.createElement('div');
  alphaPop.id = 'dsh-longtou-alpha-pop';
  alphaPop.innerHTML = `
    <h3>阿尔法构成 <span style="font-size:10px;color:var(--lt-faint);font-weight:500">评分 = 100</span></h3>
    <table>
      <tr><th>因子</th><th>权重</th><th>说明</th></tr>
      <tr><td>竞额20日比</td><td>23.57%</td><td>09:25 竞价额 ÷ 20 日调整最大单日成交额</td></tr>
      <tr><td>市场情绪</td><td>18.57%</td><td>跌停强度、涨停/跌停、天量/断板窗口</td></tr>
      <tr><td>分歧</td><td>18.57%</td><td>昨换手状态：健康/灰色/缩量/死亡；一字板为 0</td></tr>
      <tr><td>题材/地位</td><td>8.57%</td><td>目前为占位因子</td></tr>
      <tr><td>个股情绪</td><td>8.57%</td><td>独立 / 助力N：同概念低位 1–3 板数量</td></tr>
      <tr><td>事件驱动</td><td>8.57%</td><td>新闻/大盘/热点：主线、新题材、发酵、退潮</td></tr>
      <tr><td>开盘%</td><td>3.57%</td><td>-1%~+1% 满分；+1%~+4% 次之；超出为 0</td></tr>
      <tr><td>昨日成交额</td><td>10%</td><td>T-1 全天成交额 ≤3亿满分、3~10亿线性递减、≥10亿 0 分并降一档</td></tr>
    </table>
    <div class="lt-alpha-note">
      <b>权重：</b>新增昨日成交额 10%，由其余 7 个维度各平均让出 10/7≈1.43 个百分点（表中显示为四舍五入，合计 100%）。<br>
      <b>档位调整：</b>开盘 -1%~+1% 不变；+1%~+4% 加一档；&lt;-1% 或 &gt;4% 降一档；昨日成交额 &gt;10亿 再降一档。<br>
      <b>仓位：</b>禁止/不做/观察 = 0；可研究按评分：80→80%，70→60%，65→50%，60→40%，55→30%，50→20%，低于50→10%。<br>
      <b>周期状态：</b>最高板停牌仍算周期；断板第1/2天保留原最高板，第3天换新最高板。
    </div>
  `;

  // 日期面板（精确选到年-月-日）：内容在打开时按 history 现算，挂 body 上 fixed 定位
  const datePop = document.createElement('div');
  datePop.id = 'dsh-longtou-date-pop';

  mount.appendChild(fab);
  mount.appendChild(panel);
  mount.appendChild(tipEl);
  mount.appendChild(settingsPop);
  mount.appendChild(alphaPop);
  mount.appendChild(datePop);
  panel.querySelector('.lt-brand').appendChild(settingsBtn);
  panel.querySelector('.lt-brand').appendChild(alphaBtn);
  panel.querySelector('.lt-brand').appendChild(notesBtn);
  panel.querySelector('.lt-brand').appendChild(marketBtn);

  const body = panel.querySelector('#dsh-longtou-body');
  const dateEl = panel.querySelector('#dsh-longtou-date');
  const subEl = panel.querySelector('#dsh-longtou-sub');
  const statusEl = panel.querySelector('#dsh-longtou-status');
  const posEl = panel.querySelector('#dsh-longtou-pos');
  const prev = panel.querySelector('#dsh-longtou-prev');
  const next = panel.querySelector('#dsh-longtou-next');
  const latest = panel.querySelector('#dsh-longtou-latest');
  const pickBtn = panel.querySelector('#dsh-longtou-pick');
  const now = new Date();
  const todayText = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  subEl.textContent = `今天 ${todayText} · 4→5 / 3→4 / 2→3`;
  const refresh = panel.querySelector('#dsh-longtou-refresh');
  const copy = panel.querySelector('#dsh-longtou-copy');
  const pinBtn = panel.querySelector('#dsh-longtou-pin');
  const pinKey = 'dsh-longtou-pinned-v1';
  const themeBtn = panel.querySelector('#dsh-longtou-theme');
  const themeKey = 'dsh-longtou-theme-v1';
  // 颜色全部走 --lt-* 变量：只切 <html data-theme>，面板里所有颜色自动跟着变，不需要逐个改样式
  function applyTheme(theme) {
    const value = theme === 'light' ? 'light' : 'dark';
    document.documentElement.dataset.theme = value;
    try { localStorage.setItem(themeKey, value); } catch { /* 隐私模式下 localStorage 可能不可用 */ }
    themeBtn.textContent = value === 'light' ? '☾' : '☀';
    const title = value === 'light' ? '切换暗色' : '切换亮色';
    themeBtn.title = title;
    themeBtn.setAttribute('aria-label', title);
  }
  function savedTheme() {
    try {
      const value = localStorage.getItem(themeKey);
      if (value === 'light' || value === 'dark') return value;
    } catch { /* ignore */ }
    return 'dark'; // 默认暗色
  }
  const minBtn = panel.querySelector('#dsh-longtou-min');
  const maxBtn = panel.querySelector('#dsh-longtou-max');
  const head = panel.querySelector('.lt-head');
  // 按年翻页器现在挂在「选日期」面板内部（#dsh-longtou-date-pop 的首行，见 buildDatePop）。
  // 面板里的日期格子每次打开都重建，但这个弹出容器本身是静态的、只创建一次，
  // 所以监听绑在它上面只绑一次；翻页后只由 renderCycle → syncCycleYearPager 重写按钮状态
  datePop.addEventListener('click', onCyclePageClick);
  const fontRange = settingsPop.querySelector('#dsh-longtou-font-range');
  const fontValue = settingsPop.querySelector('#dsh-longtou-font-value');
  const fontKey = 'dsh-longtou-font-v1';
  const layoutKey = 'dsh-longtou-panel-layout-v1';
  const fabKey = 'dsh-longtou-fab-pos-v1';
  let dragState = null;
  let resizeState = null;
  let fabDrag = null;
  let suppressFabClick = false;
  let preSnapLayout = null;
  let tipTimer = null;
  let history = [];
  let current = 0;
  let lastMarkdown = '';
  let lastPayload = '';
  let busy = false;
  let dataRefreshTask = null; // 进行中的数据刷新任务（/refresh + /refresh.json 轮询）；二次点击直接复用

  function saveLayout() {
    try {
      localStorage.setItem(layoutKey, JSON.stringify({
        max: panel.classList.contains('lt-max'),
        snapped: panel.dataset.snapped || null,
        preSnap: preSnapLayout,
        left: panel.style.left ? parseInt(panel.style.left, 10) : null,
        top: panel.style.top ? parseInt(panel.style.top, 10) : null,
        width: panel.style.width ? parseInt(panel.style.width, 10) : null,
        height: panel.style.height ? parseInt(panel.style.height, 10) : null,
      }));
    } catch {
      /* localStorage can be disabled; dragging still works for this page */
    }
  }

  function setMaximized(value, persist = true) {
    if (value) {
      panel.classList.remove('lt-min');
      minBtn.textContent = '—';
      minBtn.title = '最小化';
      minBtn.setAttribute('aria-label', '最小化');
      delete panel.dataset.snapped;
      preSnapLayout = null;
    }
    panel.classList.toggle('lt-max', value);
    maxBtn.textContent = value ? '❐' : '□';
    maxBtn.title = value ? '还原' : '放大';
    maxBtn.setAttribute('aria-label', value ? '还原' : '放大');
    updateNarrow();
    if (persist) saveLayout();
  }

  function setMinimized(value) {
    if (value) panel.classList.remove('lt-max');
    if (value) closeDatePop(); // 最小化时 .lt-nav 隐藏，弹层必须一起收起
    panel.classList.toggle('lt-min', value);
    minBtn.textContent = value ? '▢' : '—';
    minBtn.title = value ? '展开' : '最小化';
    minBtn.setAttribute('aria-label', value ? '展开' : '最小化');
    maxBtn.textContent = '□';
    maxBtn.title = '放大';
    maxBtn.setAttribute('aria-label', '放大');
    updateNarrow();
  }

  function updateNarrow() {
    panel.classList.toggle('lt-narrow', panel.offsetWidth > 0 && panel.offsetWidth < 900);
  }

  function applyFontSize(value) {
    const base = Math.max(8, Math.min(24, Number(value) || 12));
    const scale = base / 12;
    const map = {
      table: base,
      th: base * 0.83,
      title: 17 * scale,
      sub: 10 * scale,
      date: 11 * scale,
      btn: 11 * scale,
      h2: 12 * scale,
      h3: 12 * scale,
      metric: 12 * scale,
      'metric-strong': 13 * scale,
      num: 11.5 * scale,
      code: 11 * scale,
      reason: 12 * scale,
      'reason-code': 12 * scale,
      'reason-meta': 10.5 * scale,
      chip: 10.5 * scale,
      badge: 11 * scale,
      foot: 10.5 * scale,
      pos: 10.5 * scale,
      empty: 12 * scale,
      error: 12 * scale,
    };
    Object.entries(map).forEach(([name, px]) => {
      panel.style.setProperty(`--lt-fs-${name}`, `${Math.round(px * 10) / 10}px`);
    });
    fontRange.value = String(base);
    fontValue.textContent = `${base}px`;
    try { localStorage.setItem(fontKey, String(base)); } catch { /* ignore */ }
  }

  let savedFont = 12;
  try { savedFont = Number(localStorage.getItem(fontKey)) || 12; } catch { /* ignore */ }
  applyFontSize(savedFont);

  function clampPosition(left, top, width, height) {
    const panelWidth = Number(width) || panel.offsetWidth || Math.min(980, window.innerWidth - 100);
    const panelHeight = Number(height) || panel.offsetHeight || Math.min(780, window.innerHeight - 110);
    return {
      left: Math.max(8, Math.min(Number(left) || 8, window.innerWidth - panelWidth - 8)),
      top: Math.max(8, Math.min(Number(top) || 8, window.innerHeight - panelHeight - 8)),
    };
  }

  function applyLayout() {
    try {
      const saved = JSON.parse(localStorage.getItem(layoutKey) || 'null');
      if (!saved) return;
      if (saved.max) {
        setMaximized(true, false);
        return;
      }
      if (saved.snapped && saved.preSnap) {
        panel.dataset.snapped = saved.snapped;
        preSnapLayout = saved.preSnap;
      }
      if (Number.isFinite(saved.width)) panel.style.width = `${saved.width}px`;
      if (Number.isFinite(saved.height)) panel.style.height = `${saved.height}px`;
      if (Number.isFinite(saved.left) && Number.isFinite(saved.top)) {
        const position = clampPosition(saved.left, saved.top, saved.width, saved.height);
        panel.style.left = `${position.left}px`;
        panel.style.top = `${position.top}px`;
        panel.style.right = 'auto';
        panel.style.bottom = 'auto';
      }
      updateNarrow();
    } catch {
      /* ignore malformed saved layout */
    }
  }

  function displayDate(value) {
    return value && value.length === 8
      ? `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`
      : value || '—';
  }

  // ===== 日期面板（精确选日） =====
  // history 只包含真有报告的交易日，日期不连续：这里只把实际存在的日期按月份分组画出来，
  // 不补齐非交易日、不画空位，避免伪造出不存在的日期。
  // 面板结构固定为三兄弟：翻页行（header） + 选中日/计数行 + 月份列表容器。
  // 翻页行绝不能放进月份列表容器：那段按月分组的遍历会把容器里每个子节点都当成一天/一月的宿主，
  // 上一轮就是这样把翻页行混进列表、导致标题与格子错位。这里只给骨架，内容由下面两个函数填。
  function buildDatePop() {
    // 首行是按年翻页（‹ 上一年 / 2026年 · 区间 / 下一年 ›）：翻页器属于「选日期」控件本身，
    // 不占顶部交易日那一行。面板内容每次打开都重建，所以这里只给空容器，
    // 紧接着由 syncCycleYearPager 填按钮文案与禁用态（与 renderCycle 后那次同步同一套逻辑）
    const pagerRow = '<div class="lt-date-pop-year">'
      + '<span class="lt-year-pager" id="dsh-longtou-year-pager"></span></div>';
    const curRow = '<div class="lt-date-pop-cur"><b class="lt-date-pop-cur-v">—</b>'
      + '<span class="lt-date-pop-count">0 个交易日</span></div>';
    const listBox = '<div class="lt-date-pop-months"></div>';
    datePop.innerHTML = pagerRow + curRow + listBox;
    // 与渲染月份列表同一次刷新里同步：先把按钮文案/禁用态按当前数据窗口填好，再按同一份窗口铺格子
    syncCycleYearPager();
    renderDatePopList();
  }

  // 日历「该列哪几天」与翻页器标签、情绪波浪共用同一份 cycleWindow 口径（翻年非空时整窗换成该自然年）
  function datePopWindow() {
    const data = cycleLastData && typeof cycleLastData === 'object' ? cycleLastData : cycleData;
    return cyclePageYear == null
      ? cycleWindow(data, cycleRangeMode)
      : cycleWindow(data, cycleRangeMode, cyclePageYear);
  }

  // 当前窗口覆盖的自然年集合（按出现顺序去重）；窗口为空（/cycle.json 还没到或整份为空）时返回 null，
  // 调用方退回「列出全部报告日」的老行为 —— 数据没到不能把日期面板清成空白
  function datePopYears() {
    const win = datePopWindow();
    const dates = win && Array.isArray(win.dates) ? win.dates : [];
    const years = [];
    dates.forEach((date) => {
      const year = Number(String(date).replace(/\D/g, '').slice(0, 4));
      if (Number.isInteger(year) && year > 0 && years.indexOf(year) < 0) years.push(year);
    });
    return years.length ? years : null;
  }

  // 月份列表：只按真实年月（YYYYMM）分组一次，标题与格子取自同一个 key，所以每个格子天然与它上面的标题同年同月；
  // history 已按 date 升序，月份标题因此严格递增、跨年只在真换年时才出新标题。
  // 只列当前窗口年份内的报告日（翻到 2025 就只列 2025），右上角计数 = 实际铺出来的格子数，两者永远同源
  // 「当前是哪一天」的唯一口径：情绪波浪点中某天时日视图优先（点哪看哪，绝不再回落到最新报告日），
  // 否则仍是报告历史里的 current。顶部日期控件、日期面板当前日与高亮、底栏页码全部走这一个函数，
  // 三者不可能各说各话 —— 上一轮的 bug 就是候选区换成了被点那天，而这几处显示还停在最新报告日
  // （最新日恰好是当前年份的最后一日，看起来就像「点某天却跳到该年最后一日」）
  function currentShownDate() {
    const dayKey = cycleDayDate != null ? cycleDayKey(cycleDayDate) : '';
    if (dayKey) return dayKey;
    return history[current] ? String(history[current].date || '') : '';
  }

  function renderDatePopList() {
    const listBox = datePop.querySelector('.lt-date-pop-months');
    if (!listBox) return;
    const years = datePopYears();
    const groups = [];
    const byMonth = new Map();
    history.forEach((item, index) => {
      const date = String(item.date || '');
      if (date.length !== 8) return;
      if (years && years.indexOf(Number(date.slice(0, 4))) < 0) return; // 不在当前窗口年份 → 不列
      const key = date.slice(0, 6);
      let group = byMonth.get(key);
      if (!group) {
        group = { key, days: [] };
        byMonth.set(key, group);
        groups.push(group);
      }
      group.days.push({ index, date });
    });
    const curDate = currentShownDate();
    const curEl = datePop.querySelector('.lt-date-pop-cur-v');
    if (curEl) curEl.textContent = curDate ? displayDate(curDate) : '—';
    const showed = groups.reduce((sum, group) => sum + group.days.length, 0);
    const countEl = datePop.querySelector('.lt-date-pop-count');
    if (countEl) countEl.textContent = showed + ' 个交易日';
    listBox.innerHTML = groups.map((group) => {
      const cells = group.days.map((day) => {
        const full = displayDate(day.date);
        const on = day.date === curDate ? ' on' : '';
        return '<button class="lt-day' + on + '" type="button" data-date-index="' + day.index
          + '" title="' + full + '">' + day.date.slice(6, 8) + '</button>';
      }).join('');
      return '<div class="lt-date-pop-h">' + group.key.slice(0, 4) + '年'
        + Number(group.key.slice(4, 6)) + '月</div><div class="lt-days">' + cells + '</div>';
    }).join('');
  }

  // 只在面板已打开时同步高亮与顶部日期（prev/next、轮询、指标开关都会走 renderCurrent）
  function syncDatePop() {
    if (!datePop.classList.contains('open') || !history.length) return;
    const curDate = currentShownDate();
    const curEl = datePop.querySelector('.lt-date-pop-cur-v');
    if (curEl) curEl.textContent = curDate ? displayDate(curDate) : '—';
    datePop.querySelectorAll('.lt-day').forEach((cell) => {
      const index = Number(cell.getAttribute('data-date-index'));
      const row = history[index];
      // 按日期比对（而不是按 current 下标）：日视图下 current 仍是最新报告，按它高亮就会
      // 在点某天后把高亮留在最新日 —— 看起来就是「跳到了该年最后一日」
      cell.classList.toggle('on', Boolean(row) && String(row.date || '') === curDate);
    });
  }

  function closeDatePop() {
    if (!datePop.classList.contains('open')) return;
    datePop.classList.remove('open');
    pickBtn.setAttribute('aria-expanded', 'false');
  }

  function positionDatePop() {
    if (!datePop.classList.contains('open')) return;
    const rect = pickBtn.getBoundingClientRect();
    const width = datePop.offsetWidth;
    const height = datePop.offsetHeight;
    const left = Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8));
    let top = rect.bottom + 6;
    if (top + height > window.innerHeight - 8) top = Math.max(8, rect.top - height - 6);
    datePop.style.left = `${left}px`;
    datePop.style.top = `${top}px`;
  }

  function openDatePop() {
    if (!history.length) return;
    buildDatePop();
    datePop.classList.add('open');
    pickBtn.setAttribute('aria-expanded', 'true');
    positionDatePop();
    // 打开时把选中日所在月份滚到面板中间（用 rect 差值算，避开 offsetTop 的边框/内边距误差）
    const on = datePop.querySelector('.lt-day.on');
    if (!on) { datePop.scrollTop = 0; return; }
    const cellRect = on.getBoundingClientRect();
    const popRect = datePop.getBoundingClientRect();
    datePop.scrollTop = Math.max(0,
      datePop.scrollTop + (cellRect.top - popRect.top) - datePop.clientHeight / 2 + cellRect.height / 2);
  }

  function showEmpty() {
    hideTip();
    closeDatePop(); // 没有历史可切，日期面板一并收起
    history = [];
    current = 0;
    lastMarkdown = '';
    body.innerHTML = '<div class="lt-empty"><b>还没有报告</b><span>在对话里运行 <code>longtou_report(date="20260828", stages="2,3,4")</code>，生成后点刷新。</span></div>';
    mountCycle(); // 空态也保留情绪周期区块，避免面板看起来缺一块
    dateEl.textContent = '—';
    posEl.textContent = '—';
    prev.disabled = true;
    next.disabled = true;
    latest.disabled = true;
    statusEl.textContent = '等待报告';
  }

  function hideTip() {
    clearTimeout(tipTimer);
    tipEl.classList.remove('show');
    tipEl.style.display = 'none';
    tipEl._owner = null;
  }

  function showTip(element, richHtml = '') {
    const text = element && element.dataset ? element.dataset.tip : '';
    if (!text && !richHtml) return;
    if (richHtml) {
      tipEl.innerHTML = richHtml;
    } else {
      tipEl.textContent = text;
    }
    tipEl.classList.add('show');
    tipEl.style.display = 'block';
    tipEl._owner = element;
    const rect = element.getBoundingClientRect();
    const width = tipEl.offsetWidth;
    const height = tipEl.offsetHeight;
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
    let top = rect.bottom + 8;
    if (top + height > window.innerHeight - 8) top = Math.max(8, rect.top - height - 8);
    tipEl.style.left = `${left}px`;
    tipEl.style.top = `${top}px`;
  }

  function bindTooltips() {
    panel.querySelectorAll('.lt-tip').forEach((element) => {
      if (element.dataset.bound === '1') return;
      element.dataset.bound = '1';
      element.addEventListener('mouseenter', () => {
        clearTimeout(tipTimer);
        tipTimer = setTimeout(() => showTip(element), 3000);
      });
      element.addEventListener('mouseleave', hideTip);
    });
    panel.querySelectorAll('.lt-click-tip').forEach((element) => {
      if (element.dataset.bound === '1') return;
      element.dataset.bound = '1';
      element.addEventListener('click', (event) => {
        event.stopPropagation();
        if (tipEl.classList.contains('show') && tipEl._owner === element) {
          hideTip();
          return;
        }
        clearTimeout(tipTimer);
        showTip(element);
      });
    });
    panel.querySelectorAll('button.lt-event-btn').forEach((element) => {
      if (element.dataset.bound === '1') return;
      element.dataset.bound = '1';
      element.addEventListener('click', (event) => {
        event.stopPropagation();
        if (tipEl.classList.contains('show') && tipEl._owner === element) {
          hideTip();
          return;
        }
        clearTimeout(tipTimer);
        try {
          showTip(element, eventTooltipHtml(JSON.parse(element.dataset.event || '{}')));
        } catch {
          showTip(element);
        }
      });
    });
    panel.querySelectorAll('.lt-emotion-btn').forEach((element) => {
      if (element.dataset.bound === '1') return;
      element.dataset.bound = '1';
      element.addEventListener('click', (event) => {
        event.stopPropagation();
        if (tipEl.classList.contains('show') && tipEl._owner === element) {
          hideTip();
          return;
        }
        clearTimeout(tipTimer);
        let rich = '';
        try {
          rich = element.dataset.emotion ? emotionTooltipHtml(JSON.parse(element.dataset.emotion)) : '';
        } catch {
          rich = '';
        }
        showTip(element, rich);
      });
    });
    // 「竞额20日比%」列头的排序箭头：点击切升/降序。改的是模块级 candidateSort，
    // 再走 renderCurrent 重建表格 —— 排序作用在数据数组上，箭头方向也随之刷新。
    panel.querySelectorAll('.lt-sort-btn[data-sort="ratio"]').forEach((element) => {
      if (element.dataset.bound === '1') return;
      element.dataset.bound = '1';
      element.addEventListener('click', (event) => {
        event.stopPropagation();
        candidateSort.dir = candidateSort.dir < 0 ? 1 : -1;
        renderCurrent();
      });
    });
  }

  // —— 跨表列宽同步 ——
  // 各节（2→3 / 3→4 / 4→5）是彼此独立的 <table>，auto 布局按各自内容分配列宽：
  // 4→5 可能只有一行数据，算出来的列宽和 3→4 不同，「名称」等列起点左右错开，跨表看就是文字串位。
  // 做法：先清缓存回到 auto 布局，量出同列数各表逐列宽度的最大值（既保证不窄于任何一张表的
  // 自然宽度、不会串字，又保证所有表共用同一套列宽），再以 % 写进 <colgroup> + table-layout:fixed。
  let columnResizeTimer = 0;
  let columnResizeBound = false;
  function syncTableColumns() {
    const tables = Array.from(body.querySelectorAll('table.lt-table'));
    if (!tables.length) return;
    if (!columnResizeBound) {
      columnResizeBound = true;
      window.addEventListener('resize', () => {
        clearTimeout(columnResizeTimer);
        columnResizeTimer = setTimeout(syncTableColumns, 80);
      });
    }
    tables.forEach((table) => {
      const group = table.querySelector('colgroup.lt-sync');
      if (group) group.remove();
      table.style.tableLayout = ''; // 先回到 auto 布局，否则量到的是上一轮写死的宽度
    });
    const groups = new Map();
    tables.forEach((table) => {
      const ths = Array.from(table.querySelectorAll('thead th'));
      if (ths.length < 2) return;
      // 分组键 = 列数 + 表头文本：只按列数分会把「列数相同但口径不同」的表（如观察区 9 列
      // 与龙虎榜 8 列、或同为 8 列的两张不同表）绑成一组，互相被最宽列挤坏。
      // 三张阶段表表头逐字相同，仍落在同一组，跨表对齐行为不变。
      const key = `${ths.length}|${ths.map((th) => th.textContent.trim()).join('\u0001')}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(table);
    });
    groups.forEach((list) => {
      if (list.length < 2) return; // 只有一张表时 auto 布局本来就对
      const widths = [];
      list.forEach((table) => {
        Array.from(table.querySelectorAll('thead th')).forEach((th, index) => {
          const width = th.getBoundingClientRect().width;
          widths[index] = Math.max(widths[index] || 0, width);
        });
      });
      const total = widths.reduce((sum, width) => sum + width, 0);
      if (total <= 0) return;
      list.forEach((table) => {
        const group = document.createElement('colgroup');
        group.className = 'lt-sync';
        widths.forEach((width) => {
          const col = document.createElement('col');
          // 用实测 px 而不是百分比：24 列 nowrap 内容的自然总宽常常超过容器，
          // 折成百分比会把列压窄导致串字；px 让表格保持自然总宽，溢出交给 .lt-table-wrap 横向滚动
          col.style.width = `${width.toFixed(2)}px`;
          group.appendChild(col);
        });
        table.insertBefore(group, table.firstChild);
        table.style.tableLayout = 'fixed';
      });
    });
  }

  // ===== 情绪周期区块：数据源 /cycle.json；任一部位取不到就渲染「数据待接入」占位，
  // 全部渲染都包在 try/catch 里，异常绝不外溢到下面的候选表格 =====
  const cycleUrl = '/cycle.json';
  let cycleBox = null; // 区块外壳，只建一次
  // 横向滚动位置记忆：重渲染（点某日梯队 / 60 秒轮询）会整体重建 .lt-cycle-scroll，
  // 新容器 scrollLeft 从 0 开始，用户原本看到的位置就丢了。
  // 这里在重绘前记住用户当前位置、重绘后原样还原；只有本会话第一次渲染才钉到最右（最新交易日在右端）
  let cycleScrollLeft = null; // null = 还没渲染过（首帧才钉最右）；数字 = 用户当前看到的横向位置
  let cycleData = null; // /cycle.json 原始对象；404 / 非法 JSON 时保持 null
  let cycleLoading = false;
  // 「剔除一字后最高板」紫色折线的显示开关：只记在内存里（默认关），不写 localStorage；
  // 数据源是 series[*].height_ex（当日最高板里剔除一字板后的层，与红线独立、可能更低），
  // 开关只增删这一条 polyline，不重新计算任何坐标，所以其它曲线不会跳
  let cycleMaxBoardOn = false;
  // 「压力板」折线的显示开关：默认勾选（用户裁决）。坐标、d 字符串与压力数字在渲染时算好写进
  // svg 的 data-pressure-path / data-pressure-nodes，开关只增删这段 DOM，绝不重算坐标或其他线
  let cyclePressureOn = true;
  // ===== 情绪波浪显示区间：三档（近两年 / 今年 / 去年），默认今年（ytd）=====
  // 「当前自然年」只看数据里 series 最后一条的 date（不用 new Date()），见 cycleRangeRows；
  // 值持久化在 localStorage 的 lt-cycle-range，非法值一律回落 ytd
  const CYCLE_RANGE_KEY = 'lt-cycle-range';
  const CYCLE_RANGE_KEYS = ['two', 'ytd', 'last'];
  // 按钮文案直接带年份（近两年 / 最新年 / 上一年），免得「今年 / 去年」看不出是哪一年；
  // 年份只从数据里 series 末条的 date 取，取不到时退回下面的通用文案（绝不用 new Date()）
  const CYCLE_RANGE_LABELS = { two: '近两年', ytd: '今年', last: '去年' };
  function cycleRangeYear(latestDate) {
    const stamp = String(latestDate == null ? '' : latestDate).replace(/\D/g, '');
    const year = Number(stamp.slice(0, 4));
    return Number.isFinite(year) && year > 0 ? year : null;
  }
  function cycleRangeLabels(latestDate) {
    const year = cycleRangeYear(latestDate);
    if (!year) return CYCLE_RANGE_LABELS;
    return { two: '近两年', ytd: `${year}年`, last: `${year - 1}年` };
  }
  function cycleRangeTitles(latestDate) {
    const year = cycleRangeYear(latestDate);
    if (!year) return {
      two: '近两个自然年全量（超过 260 列取最近 260 列）',
      ytd: '当前自然年年初至今',
      last: '上一自然年全年',
    };
    return {
      two: `${year - 1} + ${year} 两年全量（超过 260 列取最近 260 列）`,
      ytd: `${year} 年年初至今`,
      last: `${year - 1} 年全年`,
    };
  }
  const CYCLE_RANGE_DEFAULT = 'ytd';
  function cycleRangeSaved() {
    try {
      const raw = String(localStorage.getItem(CYCLE_RANGE_KEY) || '').trim();
      return CYCLE_RANGE_KEYS.indexOf(raw) >= 0 ? raw : CYCLE_RANGE_DEFAULT;
    } catch { return CYCLE_RANGE_DEFAULT; }
  }
  let cycleRangeMode = cycleRangeSaved();
  // 按年翻页的固定年：null = 跟随上面三档区间（加翻页之前的行为完全不变），数字 = 固定显示该自然年。
  // 与 cycleRangeMode 互斥 —— 点任一区间档位即回 null（见 applyCycleRange），两套状态不许同时生效
  let cyclePageYear = null;
  // 点情绪波浪里的某一天（梯队卡片或该列日期键）→ 面板下方的候选区切看那天的历史梯队快照。
  // null = 跟随最新交易日 —— 默认态就是老渲染路径，一个字节都不变；存的是 ladder 里那天的 date 原值，
  // 不许用 new Date() 推日期，日期只从数据里取
  let cycleDayDate = null;
  // 悬停预览的状态：浮层（首次用到才建，挂在 document.body 上以免被滚动容器裁切）
  // 与当前 SVG 里的竖线（SVG 重渲染后旧节点自然失效，下一次悬停按需重建，无需额外清理）
  let cycleHoverTip = null;
  let cycleHoverLine = null;
  // ===== 最高板折线纵轴量程拖动（v1）=====
  // 左轴上界的用户覆盖值：null = 自动（cycleHeightDomainMax）；数字 = 用户拖出来的上界。
  // 同一会话内保留（数据轮询刷新也不会丢），双击绘图区恢复自动。实际生效值还要夹在 [4, autoDomain]
  let cycleHeightDomainOverride = null;
  // 最近一次渲染用的 /cycle.json 对象：拖动时直接拿它实时重绘，不必等 60 秒轮询
  let cycleLastData = null;
  // 最近一次渲染算出的自动上界（拖动起点与 clamp 基准都取它，随数据刷新更新）
  let cycleLastAutoDomain = 0;
  // 拖动过程状态：{startX, startY, startDomain, auto, active}；active 之前仍按点击处理
  let cycleDragState = null;
  // 拖动中为 true：onCycleWaveHover 直接 return，避免拖动时竖点划线/浮层来回跳
  let cycleDragging = false;
  // 一次性标记：拖动松手后吞掉紧随其后的那次 click，避免误弹「当日详情卡」
  let cycleSuppressClick = false;
  // requestAnimationFrame 句柄：mousemove 每秒几十次，一帧最多重绘一次（实时跟随但不卡）
  let cycleDragRaf = 0;
  // 「吞下一次 click」兜底计时器的 id（模块级）：新一轮拖动开始与兜底回调都经 dropCycleDragClick 清理，
  // 上一轮残留的计时器/监听不会再吞下一轮的点击（原实现只置 false，计时器仍会回来清理新一轮的标记）
  let cycleDragClickTimer = 0;
  // 松手瞬间的坐标：只有落在同一点（浏览器补发的那次 click）才吞；位置明显不同 = 用户真的点了别处，放行
  let cycleDragClickGuard = null;

  // 压力板节点：位置与数值渲染时算好写进 data-pressure-nodes（x,y,值;x,y,值），
  // 与折线共用同一套开关机制 —— 取消勾选整组摘掉，勾选时按属性原样恢复，绝不重算坐标。
  // 数值画在同色实心圆内（黄圆深字），红线也复用同一套圆半径与居中偏移（红圆白字）
  const PRESSURE_NODE_R = 8;
  const PRESSURE_NODE_TY = 3.4;
  // ===== 家数两线 + 两条预警虚线的常量（压力线线宽的 3 倍是用户口径，用乘法表达，不写死数字）=====
  // 压力线线宽：CSS 的兜底值与之同源（.lt-cv-pressure 用 var(--lt-cv-pressure-w,1.5)），
  // 渲染时把两个线宽都注入 svg 的 style，CSS 与 JS 只有这一个真值来源
  const CYCLE_PRESSURE_STROKE = 1.5;
  const CYCLE_COUNT_STROKE = CYCLE_PRESSURE_STROKE * 3;
  // 两条横贯虚线的阈值（用户口径）：
  //   上线 = 当日涨停家数 > 100 家 → 高潮预警，警惕次日兑现；
  //   下线 = 当日最高板 ≤ 3 板 → 冰点试错，极小仓位 or 空仓
  const CYCLE_OVERHEAT_LIMIT = 100;
  const CYCLE_FREEZE_BOARD = 3;
  // ===== 两轴改为「临界点固定 + 超限压缩」=====
  // 原实现家数轴顶 = max(100, 窗口峰值×2) 自适应，100 家线会随窗口上下漂；现在把临界点钉死在
  // 固定像素位置，超出临界的那一段压成接近水平的直线，一眼能看出「某日爆表」。
  // 家数轴：0 家固定 baseY；100 家固定 y100 = padT + innerH × 0.06（贴近绘图区顶部）。
  // 0.06 的由来（用户口径「上横线再向上移动现有两横线中间的距离」）：
  //   原 0.45 时上下两横线相距 (0.84 - 0.45) × innerH = 0.39 × innerH，把上横线整体再上移这一整段
  //   → 0.45 - 0.39 = 0.06；即两线间距翻倍到 0.78 × innerH，100 家以上只剩贴近顶部的一条薄带。
  //   >100 家的部分全挤进 y100 上方那条带：100→500 家用完带高，再高软上限夹在带顶。
  //   上移后带高被 padT 卡住（y100 - padT 只剩 0.06 × innerH），实际带高取二者较小值，绝不越出绘图区。
  const CYCLE_COUNT_Y100_RATIO = 0.06; // 100 家线距绘图区顶部的内高比例（固定，不随峰值缩放）
  const CYCLE_COUNT_OVER_H = 22; // >100 家压缩带的目标像素高度（顶部余量不足时自动收窄）
  const CYCLE_COUNT_OVER_SPAN = 400; // 压缩带横跨 100→500 家；≥500 家一律贴在带顶（软上限）
  // 最高板轴：0 板固定 baseY；3 板固定 y3 = baseY - innerH × 0.16；0→3 板挤在这段矮区里，
  //   >3 板占用 y3 → padT 的正常区间（域顶仍按窗口最大板数自适应），所以破 3 板的变化被放大。
  // 比例取 0.16 而不是与家数轴镜像的 0.45：0.45 会把 0-3 板铺满 45% 内高（每板 24px，比 4 板以上
  // 每板还高），与「1-3 板挤在底部、破 3 板被放大」的口径正好相反；0.16 时 0-3 板合计约 26px、
  // 4 板以上每板约 19px（以 0-10 板域计），压缩/放大两个方向都成立。
  const CYCLE_HEIGHT_Y3_RATIO = 0.16;
  const CYCLE_HEIGHT_MIN_DOMAIN = 10; // 左轴域下限：窗口最高板不到 10 板时仍按 0-10 展开
  // ===== 左轴量程拖动的两个阈值 =====
  // 下界 4 板：3 板线固定在 y3、不随域变，但域顶至少要高过 4 板，否则 4 板会顶到绘图区上沿、
  //   曲线挤成一条水平线，看不出「破板」的变化，所以拖动最多把上界收到 4。
  const CYCLE_HEIGHT_DRAG_MIN_DOMAIN = 4;
  // 6px 消歧阈值：按下后纵向位移不足 6px 视为点击，原有「点列弹当日详情卡」行为完全不变
  const CYCLE_HEIGHT_DRAG_THRESHOLD = 6;

  // 家数 → y（纯函数，无 DOM 依赖，可单测）：
  //   null / 负值 → null（调用方据此不画点，绝不拿 0 顶替）
  //   0 家 → baseY（绘图区底部，固定）
  //   0 < 值 < 100 → [baseY, y100] 线性
  //   >100 → [y100, y100 - 带高] 线性压缩（带高 = min(CYCLE_COUNT_OVER_H, y100 - padTop)，不越顶），
  //   超过 100+SPAN 的软上限夹在带顶
  //   注意 SVG 坐标系 y 向下增大：家数越大 y 越小
  function cycleCountY(value, padTop, innerHeight) {
    const amount = cycleNum(value);
    if (amount == null || amount < 0) return null;
    const baseY = padTop + innerHeight;
    const y100 = padTop + innerHeight * CYCLE_COUNT_Y100_RATIO;
    // 上横线上移后，顶上剩余空间可能比目标带高还小：收窄到 padTop 为止，绝不画到绘图区外
    const bandHeight = Math.max(0, Math.min(CYCLE_COUNT_OVER_H, y100 - padTop));
    if (amount === 0) return baseY;
    if (amount === CYCLE_OVERHEAT_LIMIT) return y100;
    if (amount < CYCLE_OVERHEAT_LIMIT) {
      return y100 + (1 - amount / CYCLE_OVERHEAT_LIMIT) * (baseY - y100);
    }
    const over = Math.min(1, (amount - CYCLE_OVERHEAT_LIMIT) / CYCLE_COUNT_OVER_SPAN);
    return y100 - over * bandHeight;
  }

  // 左轴域（纯函数）：窗口实际最大高度向上取整，下限 CYCLE_HEIGHT_MIN_DOMAIN；空窗口回落 10
  function cycleHeightDomainMax(values) {
    const list = Array.isArray(values) ? values : [];
    const peak = list.reduce((max, value) => {
      const num = cycleNum(value);
      return num != null && num > max ? num : max;
    }, 0);
    return Math.max(CYCLE_HEIGHT_MIN_DOMAIN, Math.ceil(peak));
  }

  // 拖动 → 新的左轴上界（纯函数，无 DOM 依赖，可正则抽出单独测试；dy>0 = 往下拖）：
  //   往上拖（dy<0）量程变小 → 低位段被拉开（放大）；往下拖（dy>0）量程变大 → 放回 autoDomain。
  //   灵敏度 240px 满幅：纵向拖满 240px 正好走完 [autoDomain, minDomain] 整段。
  //   clamp 到 [minDomain, autoDomain]：
  //     下界 minDomain（=4）—— 保住 3 板线与它上方 4 板段的可见高度，否则曲线挤成一条；
  //     上界 autoDomain —— 往下拖最多回到自动量程，不放大到超出窗口实际极值（否则曲线缩在底部一条）。
  function cycleDragDomain(startDomain, dy, autoDomain, minDomain) {
    const start = cycleNum(startDomain);
    const auto = cycleNum(autoDomain);
    const min = cycleNum(minDomain);
    if (auto == null || auto <= 0) return start;
    const low = min == null ? 0 : Math.min(min, auto);
    const from = start == null ? auto : Math.max(low, Math.min(auto, start));
    const delta = cycleNum(dy) == null ? 0 : cycleNum(dy);
    const next = from + (delta * (auto - low)) / 240;
    return Math.max(low, Math.min(auto, next));
  }

  // 最高板 → y（纯函数）：
  //   null / 负值 → null（调用方不画点）；0 板 → baseY
  //   0 < 板数 < 3 → [baseY, y3] 线性压缩（1-3 板挤在底部）
  //   >3 板 → [y3, padT] 线性展开（按域顶 domainMax），超过域顶的软上限夹到 padT
  function cycleHeightY(value, domainMax, padTop, innerHeight) {
    const board = cycleNum(value);
    if (board == null || board < 0) return null;
    const baseY = padTop + innerHeight;
    const y3 = baseY - innerHeight * CYCLE_HEIGHT_Y3_RATIO;
    if (board === 0) return baseY;
    if (board === CYCLE_FREEZE_BOARD) return y3;
    if (board < CYCLE_FREEZE_BOARD) {
      return y3 + (1 - board / CYCLE_FREEZE_BOARD) * (baseY - y3);
    }
    const domainNum = cycleNum(domainMax);
    const domain = Math.max(CYCLE_FREEZE_BOARD + 1, domainNum == null ? CYCLE_HEIGHT_MIN_DOMAIN : domainNum);
    const ratio = Math.min(1, (board - CYCLE_FREEZE_BOARD) / (domain - CYCLE_FREEZE_BOARD));
    return y3 - ratio * (y3 - padTop);
  }
  function pressureNodePoints(raw) {
    return String(raw || '').split(';').filter(Boolean).map((chunk) => {
      const cell = chunk.split(',');
      if (cell.length < 3) return null;
      const x = Number(cell[0]);
      const y = Number(cell[1]);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
      return { x, y, value: cell[2] };
    }).filter(Boolean);
  }
  function pressureNodeMarkup(raw) {
    return pressureNodePoints(raw)
      .map((item) => `<circle class="lt-cv-pressure-node" cx="${item.x.toFixed(1)}" cy="${item.y.toFixed(1)}" r="${PRESSURE_NODE_R}"/>`
        + `<text class="lt-cv-pressure-num" x="${item.x.toFixed(1)}" y="${(item.y + PRESSURE_NODE_TY).toFixed(1)}" text-anchor="middle">${item.value}</text>`)
      .join('');
  }
  function appendCyclePressureNodes(group, raw) {
    pressureNodePoints(raw).forEach((item) => {
      const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      circle.setAttribute('class', 'lt-cv-pressure-node');
      circle.setAttribute('cx', item.x.toFixed(1));
      circle.setAttribute('cy', item.y.toFixed(1));
      circle.setAttribute('r', String(PRESSURE_NODE_R));
      group.appendChild(circle);
      const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      text.setAttribute('class', 'lt-cv-pressure-num');
      text.setAttribute('x', item.x.toFixed(1));
      text.setAttribute('y', (item.y + PRESSURE_NODE_TY).toFixed(1));
      text.setAttribute('text-anchor', 'middle');
      text.textContent = item.value;
      group.appendChild(text);
    });
  }

  // 孤立单点段坐标（x,y;x,y）：这类段没有连线可画，用一个小的同色圆点充当标记。
  // 该标记与 node 圆同色、又比 node 圆小，最终被 node 圆（r=8）完全覆盖 → 视觉上只由
  // node 圆+数字表达；保留它使「取消勾选再勾选」重建出的 DOM 与首帧渲染完全一致
  function pressureDotPoints(raw) {
    return String(raw || '').split(';').filter(Boolean).map((chunk) => {
      const cell = chunk.split(',');
      if (cell.length < 2) return null;
      const x = Number(cell[0]);
      const y = Number(cell[1]);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
      return { x, y };
    }).filter(Boolean);
  }
  function appendCyclePressureDots(group, raw) {
    pressureDotPoints(raw).forEach((item) => {
      const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      circle.setAttribute('class', 'lt-cv-pressure-pt');
      circle.setAttribute('cx', item.x.toFixed(1));
      circle.setAttribute('cy', item.y.toFixed(1));
      circle.setAttribute('r', '2.5');
      group.appendChild(circle);
    });
  }

  // 压力板图形：分段 path（突破处断线，多段拼进同一个 d）+ 孤立单点的兜底小圆点 +
  // 每个压力点的同色圆与圆内数字（圆+数字覆盖所有点，含突破日与孤立单点）
  function cyclePressureMarkup(d, dots, nodes) {
    const markup = d ? `<path class="lt-cv-pressure" d="${d}"/>` : '';
    const dotsMarkup = pressureDotPoints(dots)
      .map((item) => `<circle class="lt-cv-pressure-pt" cx="${item.x.toFixed(1)}" cy="${item.y.toFixed(1)}" r="2.5"/>`)
      .join('');
    return markup + dotsMarkup + pressureNodeMarkup(nodes);
  }

  // 图例勾选框 → 增删紫色 polyline 与紫线数值标签。点坐标渲染时已写进 svg 的
  // data-maxboard-points / data-maxboard-labels，这里仅按需恢复，绝不按可见线重算 domain
  function applyCycleMaxBoardToggle(input) {
    if (!input) return;
    cycleMaxBoardOn = !!input.checked;
    const part = input.closest ? input.closest('.lt-cycle-part') : null;
    const svg = part ? part.querySelector('svg.lt-cv') : null;
    if (!svg) return;
    const existing = svg.querySelector('.lt-cv-line-max');
    if (!cycleMaxBoardOn) {
      if (existing) existing.remove();
      const labels = svg.querySelector('.lt-cv-ex-labels');
      if (labels) labels.remove();
      return;
    }
    const points = svg.getAttribute('data-maxboard-points');
    if (points && !existing) {
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
      line.setAttribute('class', 'lt-cv-line-max');
      line.setAttribute('points', points);
      svg.appendChild(line); // 追加到末尾：紫色线压在最上层
    }
    appendCycleExLabels(svg);
  }

  // 紫线数值标签：位置与数值渲染时写进 data-maxboard-labels（x,y,值;x,y,值），
  // 勾选时才生成 DOM（整组 .lt-cv-ex-labels），取消时整组摘掉 → 默认不勾选时无标签也无线
  function appendCycleExLabels(svg) {
    if (!svg || svg.querySelector('.lt-cv-ex-labels')) return;
    const raw = svg.getAttribute('data-maxboard-labels') || '';
    if (!raw) return;
    const group = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    group.setAttribute('class', 'lt-cv-ex-labels');
    raw.split(';').forEach((chunk) => {
      const cell = chunk.split(',');
      if (cell.length < 3) return;
      const x = Number(cell[0]);
      const y = Number(cell[1]);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return;
      const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      text.setAttribute('class', 'lt-cv-val-ex');
      text.setAttribute('x', x.toFixed(1));
      // 紫线标签放点下方（红线标签在点上方 y-7），两套数字不错位重叠
      text.setAttribute('y', (y + 15).toFixed(1));
      text.setAttribute('text-anchor', 'middle');
      text.textContent = cell[2];
      group.appendChild(text);
    });
    if (group.childNodes.length) svg.appendChild(group);
  }

  // 压力板开关：取消 → 清空 group 里的分段 path、压力圆与圆内数字；勾选 → 从 data-* 原样恢复，不重算任何坐标
  function applyCyclePressureToggle(input) {
    if (!input) return;
    cyclePressureOn = !!input.checked;
    const part = input.closest ? input.closest('.lt-cycle-part') : null;
    const svg = part ? part.querySelector('svg.lt-cv') : null;
    if (!svg) return;
    const group = svg.querySelector('.lt-cv-pressure-group');
    if (!group) return;
    while (group.firstChild) group.removeChild(group.firstChild);
    if (!cyclePressureOn) return;
    const d = svg.getAttribute('data-pressure-path') || '';
    if (d) {
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('class', 'lt-cv-pressure');
      path.setAttribute('d', d);
      group.appendChild(path);
    }
    // 恢复顺序与首帧一致：path → 孤立单点兜底小圆点 → 每个压力点的圆+圆内数字
    appendCyclePressureDots(group, svg.getAttribute('data-pressure-dots'));
    appendCyclePressureNodes(group, svg.getAttribute('data-pressure-nodes'));
  }

  // 点击当日列 → 用现成浮层（tipEl / showTip）显示当日涨停/跌停家数与两条预警线的触发情况。
  // 家数两条粗线本身不写数字，数值只在这里出现；再点同一列收起，与其它 lt-click-tip 行为一致
  function onCycleDayClick(event) {
    const target = event.target;
    const card = target && target.closest ? target.closest('.lt-cycle-col.lt-cycle-col-click') : null;
    if (!card || !cycleBox || !cycleBox.contains(card)) return;
    event.stopPropagation();
    if (tipEl.classList.contains('show') && tipEl._owner === card) {
      hideTip();
      return;
    }
    clearTimeout(tipTimer);
    showTip(card);
  }

  // 点情绪波浪里的某一天（梯队卡片或该列日期键）→ 下方候选区切看那天的历史梯队快照。
  // 日期键行本身不塞任何元素 / 属性（列网格多一个节点就会与梯队列错列），所以日期键靠列序号反查：
  // 序号 → 日期一律走与 renderCycleWave 同一个 cycleWindow 算式，三者永远是同一天
  function cycleDateAtColumn(index) {
    const data = cycleLastData && typeof cycleLastData === 'object' ? cycleLastData : cycleData;
    if (!data || typeof data !== 'object' || !Number.isInteger(index) || index < 0) return '';
    const win = cyclePageYear == null
      ? cycleWindow(data, cycleRangeMode)
      : cycleWindow(data, cycleRangeMode, cyclePageYear);
    const dates = win && Array.isArray(win.dates) ? win.dates : [];
    return index >= 0 && index < dates.length ? String(dates[index] || '') : '';
  }

  // 选中/取消某一天：再点同一天 = 取消回最新，与状态条上的「回到最新」同一个出口。
  // 换日只需重绘下方候选区（renderCurrent 里走 cycleDayDate != null 的分支），图表不用重建
  function cyclePickDay(date) {
    const key = cycleDayKey(date);
    if (!key) return;
    cycleDayDate = key === cycleDayDate ? null : key;
    hideTip(); // 当日详情卡锚在旧卡片上，候选区一换它就成孤儿了
    renderCurrent();
  }

  // 选中日的点击入口：梯队卡片（.lt-cycle-col-click，带 data-date）或该列日期键（.lt-cycle-axis span）。
  // 与 onCycleDayClick（弹当日家数浮层）并行挂在 cycleBox 上，互不吞事件
  function onCycleDayPick(event) {
    const target = event.target;
    if (!target || !target.closest || !cycleBox || !cycleBox.contains(target)) return;
    const card = target.closest('.lt-cycle-col.lt-cycle-col-click');
    if (card) {
      event.preventDefault();
      event.stopPropagation();
      cyclePickDay(card.getAttribute('data-date'));
      return;
    }
    const key = target.closest('.lt-cycle-axis span');
    if (!key) return;
    const index = Array.from(cycleBox.querySelectorAll('.lt-cycle-axis span')).indexOf(key);
    if (index < 0) return;
    event.preventDefault();
    event.stopPropagation();
    cyclePickDay(cycleDateAtColumn(index));
  }

  // ===== 悬停预览：吸附到最近一列的竖点划线 + 该日家数浮层 =====
  // 与点击选中态完全独立：这里只在 SVG 末尾追加一条竖线（因此盖在所有折线之上）、
  // 再加一个只读小浮层，不改任何既有 DOM、不拦 click、也不写任何选中状态。
  // 「鼠标离开 SVG 就隐藏」靠 mouseover 目标判定 + cycleBox 的 mouseleave，两处都会收起。
  function cycleHoverTipEl() {
    if (cycleHoverTip && cycleHoverTip.parentNode) return cycleHoverTip;
    cycleHoverTip = document.createElement('div');
    cycleHoverTip.className = 'lt-cv-hover-tip';
    document.body.appendChild(cycleHoverTip);
    return cycleHoverTip;
  }

  function hideCycleHover() {
    if (cycleHoverTip) cycleHoverTip.style.display = 'none';
    if (cycleHoverLine && cycleHoverLine.parentNode) cycleHoverLine.parentNode.removeChild(cycleHoverLine);
    cycleHoverLine = null;
  }

  function onCycleWaveHover(event) {
    // 拖动纵轴量程期间不显示悬停竖点划线/浮层：由拖动逻辑独占鼠标，避免两套跟随互相打架
    if (cycleDragging) return;
    const target = event.target;
    const svg = target && target.closest ? target.closest('svg.lt-cv') : null;
    if (!svg || !cycleBox || !cycleBox.contains(svg)) { hideCycleHover(); return; }
    // 列网格参数在渲染时写进 svg 的 data-*，悬停时不再重算几何
    const cols = Number(svg.getAttribute('data-cols'));
    const padL = Number(svg.getAttribute('data-padl'));
    const colw = Number(svg.getAttribute('data-colw'));
    const gap = Number(svg.getAttribute('data-gap'));
    const padT = Number(svg.getAttribute('data-padt'));
    const baseY = Number(svg.getAttribute('data-basey'));
    if (!(cols > 0) || !Number.isFinite(padL) || !Number.isFinite(colw) || !Number.isFinite(gap)
      || !Number.isFinite(padT) || !Number.isFinite(baseY)) {
      hideCycleHover();
      return;
    }
    const rect = svg.getBoundingClientRect();
    if (!rect.width || !rect.height) { hideCycleHover(); return; }
    // 浏览器缩放时 CSS 像素与 viewBox 单位可能不等，按实际渲染宽度换算成用户坐标
    const viewWidth = svg.viewBox && svg.viewBox.baseVal ? svg.viewBox.baseVal.width : rect.width;
    const x = (event.clientX - rect.left) * (viewWidth ? viewWidth / rect.width : 1);
    // 按鼠标 x 反算列索引并夹到 [0, n-1]：竖线吸附到该列中心，视觉上按列跳动
    const index = Math.max(0, Math.min(cols - 1, Math.round((x - padL - colw / 2) / (colw + gap))));
    // 该日文案完全复用梯队卡片现成的 data-date / data-tip：data-tip 的第一段就是
    // renderCycleLadderCard 里 countText 拼出的「涨停 N 家 / 跌停 M 家」，缺失日仍是「—（当日数据未更新）」
    const part = svg.closest ? svg.closest('.lt-cycle-part') : null;
    let cards = svg._ltHoverCards;
    if (!cards) {
      cards = part ? Array.from(part.querySelectorAll('.lt-cycle-ladder .lt-cycle-col')) : [];
      svg._ltHoverCards = cards;
    }
    const card = cards[index] || null;
    const digits = String(card ? card.getAttribute('data-date') || '' : '').replace(/\D/g, '');
    const dateText = digits.length >= 8
      ? `${digits.slice(0, 4)}-${digits.slice(4, 6)}-${digits.slice(6, 8)}`
      : (digits || '—');
    const rawTip = card ? String(card.getAttribute('data-tip') || '') : '';
    const countText = rawTip.split(' · ')[0] || '—（当日数据未更新）';
    const tip = cycleHoverTipEl();
    tip.innerHTML = `<b>${esc(dateText)}</b><br>${esc(countText)}`;
    tip.style.display = 'block';
    // 浮层跟随鼠标横向移动；贴近视口右边缘时翻到鼠标左侧，纵向跟随并夹在视口内
    const tipW = tip.offsetWidth;
    const tipH = tip.offsetHeight;
    let left = event.clientX + 14;
    if (left + tipW > window.innerWidth - 8) left = event.clientX - tipW - 14;
    left = Math.max(8, Math.min(left, window.innerWidth - tipW - 8));
    const top = Math.max(8, Math.min(event.clientY + 14, window.innerHeight - tipH - 8));
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
    // 竖线：append 在 SVG 最后 → 绘制顺序最后 → 盖在所有折线/圆点之上，不会被折线挡住
    if (!cycleHoverLine || cycleHoverLine.parentNode !== svg) {
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('class', 'lt-cv-cursor');
      line.setAttribute('stroke-dasharray', '3 3 1 3');
      line.setAttribute('stroke-width', '1');
      line.setAttribute('pointer-events', 'none');
      line.setAttribute('aria-hidden', 'true');
      line.setAttribute('y1', String(padT));
      line.setAttribute('y2', String(baseY));
      svg.appendChild(line);
      cycleHoverLine = line;
    }
    const cx = (padL + index * (colw + gap) + colw / 2).toFixed(1);
    cycleHoverLine.setAttribute('x1', cx);
    cycleHoverLine.setAttribute('x2', cx);
  }

  // ===== 最高板纵轴量程拖动（v1）：只改左轴（右轴家数轴 cycleCountY 与所有家数图元绝不参与）=====
  // 按下 → 记起点；纵向位移 <6px 视为点击（原有「点当日列弹详情卡」完全不变）；
  // ≥6px 且 |dy|>|dx| 才进入拖动态，之后才收起悬停、改光标、吞 click。
  function cycleSvgOf(target) {
    return target && target.closest ? target.closest('svg.lt-cv') : null;
  }

  // 清掉「吞下一次 click」这层防护：摘 window click 监听 + 清兜底计时器 + 复位标记与松手坐标。
  // onCycleDragStart、mouseup 后的重新布置、兜底计时器回调三处共用，保证每轮拖动只留一套
  function dropCycleDragClick() {
    cycleSuppressClick = false;
    cycleDragClickGuard = null;
    window.removeEventListener('click', swallowCycleDragClick, true);
    if (cycleDragClickTimer) {
      window.clearTimeout(cycleDragClickTimer);
      cycleDragClickTimer = 0;
    }
  }

  // 拖动期间锁文字选中：纵向拖动会顺手选中 SVG 里的刻度/图例文字（蓝色选区）。
  // 直接改 body 内联样式，结束置空即恢复原 CSS 值；只作用于拖动态，不影响其它区域
  function setCycleDragSelectLock(locked) {
    if (!document.body) return;
    const value = locked ? 'none' : '';
    document.body.style.userSelect = value;
    document.body.style.webkitUserSelect = value;
  }

  // 切到别的标签页/最小化：mouseup 必然收不到，按取消处理；只在 hidden 时收，
  // 避免来回切标签时把正在进行的拖动误判成结束
  function onCycleDragVisibilityChange() {
    if (document.visibilityState === 'hidden') onCycleDragEnd(null);
  }

  function onCycleDragStart(event) {
    if (event.button !== 0) return;
    const target = event.target;
    // 起点必须落在绘图区（SVG 或其子元素）里；图例勾选框/表单控件上的按下交给原有点击逻辑
    const svg = cycleSvgOf(target);
    if (!svg || !cycleBox || !cycleBox.contains(svg)) return;
    if (target.closest && target.closest('input, button, select, textarea, a, .lt-cycle-legend')) return;
    const auto = cycleLastAutoDomain;
    if (!(auto > 0)) return;
    // 新一轮先把上一轮（典型是拖到窗口外松手、没等到 click 的那轮）的 click 监听与兜底计时器摘干净，
    // 否则旧标记会在本轮按下后继续存活，把用户此刻点梯队列的 click 吞掉
    dropCycleDragClick();
    // 起点量程 = 按下瞬间屏幕上生效的 domainMax（覆盖值已夹好，与 renderCycleWave 同一算式）
    const startDomain = cycleHeightDomainOverride == null
      ? auto
      : Math.min(auto, Math.max(CYCLE_HEIGHT_DRAG_MIN_DOMAIN, cycleHeightDomainOverride));
    cycleDragState = {
      startX: event.clientX,
      startY: event.clientY,
      startDomain,
      auto,
      active: false,
    };
    // window 上挂监听：拖出图区甚至拖出窗口也能收到 mouseup，光标不会卡在 ns-resize
    window.addEventListener('mousemove', onCycleDragMove, true);
    window.addEventListener('mouseup', onCycleDragEnd, true);
    // 兜底收尾（Alt+Tab 切走、指针被系统取消）：这些场景没有 mouseup，若不结束拖动，
    // cycleDragState 会一直挂着 → 之后无按键的 mousemove 仍按旧 startY 改量程、body 光标卡 ns-resize
    window.addEventListener('blur', onCycleDragEnd, true);
    window.addEventListener('pointercancel', onCycleDragEnd, true);
    document.addEventListener('visibilitychange', onCycleDragVisibilityChange);
  }

  function onCycleDragMove(event) {
    const state = cycleDragState;
    if (!state) return;
    const dx = event.clientX - state.startX;
    const dy = event.clientY - state.startY;
    if (!state.active) {
      // 6px 阈值 + 纵向优先：位移不足或横向为主（横向滚动/框选）都不算拖动，不抢点击
      if (Math.abs(dy) < CYCLE_HEIGHT_DRAG_THRESHOLD || Math.abs(dy) <= Math.abs(dx)) return;
      state.active = true;
      cycleDragging = true;
      hideCycleHover();
      document.body.style.cursor = 'ns-resize';
      setCycleDragSelectLock(true); // 进入拖动态就锁选中，避免拖出一片蓝色选区
    }
    event.preventDefault();
    // 板数是离散值：写入前吸附到整数板，左轴顶端刻度与图例（左轴 0-N）就不会出现 4.37 这种小数。
    // 吸附放在调用处，cycleDragDomain 的签名与公式语义保持不变；夹紧后的值仍落在整数区间 [4, auto]
    const next = cycleDragDomain(state.startDomain, dy, state.auto, CYCLE_HEIGHT_DRAG_MIN_DOMAIN);
    cycleHeightDomainOverride = Math.round(next);
    scheduleCycleWaveRedraw(); // 每帧最多重绘一次 → 实时跟随
  }

  // 结束拖动：mouseup / blur / pointercancel / visibilitychange(hidden) 都走这里
  function onCycleDragEnd(event) {
    // 左键拖动中按下并松开右键会先来一次 button=2 的 mouseup：不校验就会提前结束拖动并多吞一次 click。
    // blur/pointercancel/visibilitychange 没有 button，只能按类型区分，不能一律取 event.button 判断
    if (event && event.type === 'mouseup' && event.button !== 0) return;
    const state = cycleDragState;
    window.removeEventListener('mousemove', onCycleDragMove, true);
    window.removeEventListener('mouseup', onCycleDragEnd, true);
    window.removeEventListener('blur', onCycleDragEnd, true);
    window.removeEventListener('pointercancel', onCycleDragEnd, true);
    document.removeEventListener('visibilitychange', onCycleDragVisibilityChange);
    cycleDragState = null;
    if (!state || !state.active) return; // 没越过阈值 = 点击：原有点击流程照旧
    cycleDragging = false;
    document.body.style.cursor = '';
    setCycleDragSelectLock(false);
    if (cycleDragRaf) { cancelAnimationFrame(cycleDragRaf); cycleDragRaf = 0; }
    redrawCycleWave(); // 松手补最后一帧，保证落点量程一定画出来
    // 只有 mouseup 收尾才会紧跟一次浏览器补发的 click；blur/pointercancel/visibility 收尾（Alt+Tab、
    // 拖到窗口外松手）根本没有那次 click，此时挂 300ms 吞点击标记只会误吞用户紧接着点梯队列的那一下
    if (!event || event.type !== 'mouseup') return;
    // 松手后浏览器还会补一次 click：捕获阶段一次性吞掉，避免误弹当日详情卡。
    // 每轮独立：先摘掉上一轮可能残留的监听/计时器，再挂本轮的
    dropCycleDragClick();
    cycleSuppressClick = true;
    cycleDragClickGuard = { x: event.clientX, y: event.clientY };
    window.addEventListener('click', swallowCycleDragClick, true);
    // 兜底：万一没等到那次 click（比如拖出窗口松手），300ms 后自动清标记，绝不吞后续正常点击
    cycleDragClickTimer = window.setTimeout(dropCycleDragClick, 300);
  }

  function swallowCycleDragClick(event) {
    if (!cycleSuppressClick) return;
    // 松手点之外的 click = 用户真的要点别处（详情卡要能弹出来）：直接放行并撤掉这层防护；
    // 只有落在松手点附近的那次 click 才是浏览器给拖动的补发事件，才吞
    const guard = cycleDragClickGuard;
    const farFromRelease = guard != null
      && (Math.abs(event.clientX - guard.x) > 4 || Math.abs(event.clientY - guard.y) > 4);
    dropCycleDragClick();
    if (farFromRelease) return;
    event.stopPropagation();
    event.preventDefault();
  }

  // 双击绘图区 = 回到自动量程（override 置 null 后重绘，图例/刻度/折线一起回到 0-autoDomain）
  function onCycleWaveDblClick(event) {
    const svg = cycleSvgOf(event.target);
    if (!svg || !cycleBox || !cycleBox.contains(svg)) return;
    if (cycleHeightDomainOverride == null) return;
    cycleHeightDomainOverride = null;
    redrawCycleWave();
  }

  function scheduleCycleWaveRedraw() {
    if (cycleDragRaf) return;
    const raf = typeof requestAnimationFrame === 'function'
      ? requestAnimationFrame
      : (callback) => window.setTimeout(callback, 16);
    cycleDragRaf = raf(() => {
      cycleDragRaf = 0;
      redrawCycleWave();
    });
  }

  // 拖动只换「波浪图 SVG + 图例」两块，不整块重建 cycleBox：梯队卡片的竖向滚动位置不会跳，
  // 也不会每帧重排 200+ 列 DOM（实时跟随要的是重绘够快，不是整块 innerHTML 重来）
  function redrawCycleWave() {
    if (!cycleBox || !cycleLastData) return;
    const svg = cycleBox.querySelector('svg.lt-cv');
    if (!svg) return;
    const part = svg.closest ? svg.closest('.lt-cycle-part') : null;
    if (!part) return;
    const holder = document.createElement('div');
    holder.innerHTML = renderCycleWave(cycleLastData);
    const nextSvg = holder.querySelector('svg.lt-cv');
    if (!nextSvg) return;
    svg.replaceWith(nextSvg);
    // 图例里「最高板（左轴 0-N）」跟着新量程走
    const currentLegend = part.querySelector('.lt-cycle-legend');
    const nextLegend = holder.querySelector('.lt-cycle-legend');
    if (currentLegend && nextLegend) currentLegend.replaceWith(nextLegend);
  }

  function onCycleLegendChange(event) {
    const target = event.target;
    if (target && target.classList && target.classList.contains('lt-cv-max-toggle')) {
      applyCycleMaxBoardToggle(target);
    }
    if (target && target.classList && target.classList.contains('lt-cv-pressure-toggle')) {
      applyCyclePressureToggle(target);
    }
  }

  // 区间三档切换按钮：图例最左侧一组小胶囊，选中档位来自 cycleRangeMode（重绘时按同一状态再生成）。
  // data 用于取 series 末条日期里的年份，让按钮直接显示「2026年 / 2025年」而不是模糊的「今年 / 去年」
  function cycleRangeToggleMarkup(data) {
    const seriesAll = data && Array.isArray(data.series) ? data.series : [];
    const lastRow = seriesAll.length ? seriesAll[seriesAll.length - 1] : null;
    const latestDate = lastRow && typeof lastRow === 'object' ? lastRow.date : '';
    const labels = cycleRangeLabels(latestDate);
    const titles = cycleRangeTitles(latestDate);
    const buttons = CYCLE_RANGE_KEYS.map((key) => {
      const on = key === cycleRangeMode;
      return `<button type="button" class="lt-range-btn${on ? ' lt-range-on' : ''}" data-cycle-range="${key}"`
        + ` aria-pressed="${on ? 'true' : 'false'}" title="${titles[key]}">${labels[key]}</button>`;
    }).join('');
    return `<span class="lt-cycle-range" role="group" aria-label="显示区间">${buttons}</span>`;
  }

  // 切区间：写回 localStorage → 左轴量程回自动（清掉拖拽覆盖值）→ 清悬停/选中浮层残留 → 整块重渲染。
  // 重渲染后波浪图、压力线、家数线、两条预警线与连板梯队列全部换成同一份裁剪后的 rows，不可能混年份
  function applyCycleRange(mode) {
    const next = CYCLE_RANGE_KEYS.indexOf(mode) >= 0 ? mode : CYCLE_RANGE_DEFAULT;
    // 区间档与「固定年翻页」互斥：点任一档位一律回区间视图。
    // 先复位再判「档位没变」—— 停在 2025 年时点当前已高亮的「2026年」，档位没变但必须回到区间视图
    const hadPage = cyclePageYear != null;
    cyclePageYear = null;
    if (next === cycleRangeMode && !hadPage) return;
    cycleRangeMode = next;
    try { localStorage.setItem(CYCLE_RANGE_KEY, next); } catch { /* 隐私模式下 localStorage 可能不可用 */ }
    cycleHeightDomainOverride = null; // 拖拽出来的量程作废，回自动
    cycleLastAutoDomain = 0;
    dropCycleDragClick(); // 清掉「吞下一次 click」的监听与兜底计时器，避免影响切换后的点击
    setCycleDragSelectLock(false);
    hideCycleHover(); // 悬停竖线 + 浮层（旧 SVG 已随重渲染销毁，这里同时清掉失效引用）
    hideTip(); // 当日详情卡（选中残留在旧卡片上，整块重建后已不存在，浮层也要收）
    // 换了一整段窗口（不是「原地重绘」）：横向位置作废，重新钉到新窗口最右，与修复前语义一致
    cycleScrollLeft = null;
    renderCycle();
  }

  // 事件委托：区间按钮（renderCycle 整块重建 body 时按钮跟着换，监听只挂在 cycleBox 上）
  function onCycleRangeClick(event) {
    const target = event.target;
    const button = target && target.closest ? target.closest('[data-cycle-range]') : null;
    if (!button || !cycleBox || !cycleBox.contains(button)) return;
    event.preventDefault();
    event.stopPropagation();
    applyCycleRange(button.getAttribute('data-cycle-range'));
    // 切档后选中日可能被新窗口裁掉：清掉并重绘候选区，绝不留下「图表在这里、候选还停在别处」
    syncCycleDayAfterWindow();
  }

  // ===== 日期键上方的「← 上一年 / 下一年 →」翻页 =====
  // 数据覆盖的自然年跨度：最早/最新年与「哪一年有数据」都只从 rows 的 date 前四位取，绝不用 new Date()，
  // 与 cycleRangeRows 判年份的口径完全一致；series 与 ladder 一起算，避免某一侧缺年导致翻到空图
  function cycleYearSpan(data) {
    const source = data && typeof data === 'object' ? data : {};
    const seriesRows = (Array.isArray(source.series) ? source.series : []).filter((row) => row && typeof row === 'object');
    const ladderRows = (Array.isArray(source.ladder) ? source.ladder : []).filter((col) => col && typeof col === 'object');
    const yearsOf = (rows) => rows
      .map((row) => Number(String(row.date == null ? '' : row.date).replace(/\D/g, '').slice(0, 4)))
      .filter((year) => Number.isInteger(year) && year > 0);
    const years = yearsOf(seriesRows).concat(yearsOf(ladderRows));
    if (!years.length) return null;
    return { series: seriesRows, ladder: ladderRows, min: Math.min(...years), max: Math.max(...years) };
  }

  // 某一年到底有没有数据：先看 series（日期键的数据源），没有再看 ladder，两侧都没有才算空年
  function cycleYearHasRows(span, year) {
    if (!span) return false;
    if (cycleRowsOfYear(span.series, year).length) return true;
    return cycleRowsOfYear(span.ladder, year).length > 0;
  }

  // 翻页条 markup：容器是「选日期」面板的首行（#dsh-longtou-date-pop 里的 #dsh-longtou-year-pager），
  // 不进交易日那一行、也不进日期键行 —— 日期键行是列网格，塞任何额外元素都会把日期键与梯队卡片错开。
  // 区间视图下标签带「· 区间」后缀，让用户看得出当前不是某一整年；翻页基准取最新数据年而非当前显示年，
  // 所以「去年」档下点「上一年」也是去 2025 整年而不是回到 2024（数据里没有 2024）
  function cyclePagerMarkup(data, win) {
    const span = cycleYearSpan(data);
    const dates = win && Array.isArray(win.dates) ? win.dates : [];
    const fixed = Number.isInteger(cyclePageYear) && cyclePageYear > 0 ? cyclePageYear : null;
    const rightDate = dates.length ? dates[dates.length - 1] : '';
    const shownYear = fixed != null ? fixed : cycleRangeYear(rightDate);
    const baseYear = fixed != null ? fixed : (span ? span.max : cycleRangeYear(rightDate));
    const prevYear = baseYear == null ? null : baseYear - 1;
    const nextYear = baseYear == null ? null : baseYear + 1;
    const canPrev = prevYear != null && span != null && prevYear >= span.min && cycleYearHasRows(span, prevYear);
    const canNext = nextYear != null && span != null && nextYear <= span.max && cycleYearHasRows(span, nextYear);
    const label = shownYear == null ? '—' : fixed != null ? `${shownYear}年` : `${shownYear}年 · 区间`;
    const prevTitle = canPrev ? `翻到 ${prevYear} 年全年` : '已是数据里最早的一年';
    const nextTitle = canNext ? `翻到 ${nextYear} 年全年` : '已是数据里最新的一年';
    return '<div class="lt-cycle-pager" role="group" aria-label="按自然年翻页">'
      + `<button type="button" class="lt-range-btn lt-cycle-pager-prev" data-cycle-page="prev" title="${esc(prevTitle)}"`
      + `${canPrev ? '' : ' disabled aria-disabled="true"'}>‹ 上一年</button>`
      + `<span class="lt-cycle-pager-label">${esc(label)}</span>`
      + `<button type="button" class="lt-range-btn lt-cycle-pager-next" data-cycle-page="next" title="${esc(nextTitle)}"`
      + `${canNext ? '' : ' disabled aria-disabled="true"'}>下一年 ›</button>`
      + '</div>';
  }

  // 翻页：基准年 = 当前固定年（区间视图下取最新数据年），目标年 = 基准 ± 1，夹在数据最早/最新年之间；
  // 目标年没数据就同方向再跳（最多 3 跳），跳不动就原地不动 —— 宁可不翻，也不给一块空白图
  function applyCyclePage(direction) {
    const step = direction === 'prev' ? -1 : direction === 'next' ? 1 : 0;
    if (!step) return;
    const data = cycleLastData && typeof cycleLastData === 'object' ? cycleLastData : cycleData;
    const span = cycleYearSpan(data);
    if (!span) return;
    const fixed = Number.isInteger(cyclePageYear) && cyclePageYear > 0 ? cyclePageYear : null;
    let target = (fixed != null ? fixed : span.max) + step;
    for (let hop = 0; hop < 3 && target >= span.min && target <= span.max && !cycleYearHasRows(span, target); hop += 1) {
      target += step;
    }
    if (target < span.min || target > span.max || !cycleYearHasRows(span, target)) return;
    if (target === cyclePageYear) return; // 已经在这一年，不白重绘
    cyclePageYear = target;
    // 与切区间同一套清理：左轴量程回自动、清拖拽/悬停/当日详情残留，再整块重渲染
    cycleHeightDomainOverride = null;
    cycleLastAutoDomain = 0;
    dropCycleDragClick();
    setCycleDragSelectLock(false);
    hideCycleHover();
    hideTip();
    // 翻年也是整段换窗口：横向位置作废，重新钉到该年最右
    cycleScrollLeft = null;
    renderCycle();
  }

  // 翻页器在「选日期」面板首行（#dsh-longtou-year-pager），面板内容每次打开由 buildDatePop 重建并调用本函数，
  // renderCycle 后也调一次刷新禁用态；监听挂在静态的 datePop 上只绑一次，不会重复绑定。
  // window 用与 renderCycleWave 同一套裁剪口径算，标签显示的年份与图表里实际铺开的那一年永远一致
  function syncCycleYearPager() {
    const box = document.getElementById('dsh-longtou-year-pager');
    if (!box) return;
    const data = cycleLastData && typeof cycleLastData === 'object' ? cycleLastData : cycleData;
    box.innerHTML = cyclePagerMarkup(data, datePopWindow());
    // 禁用态只认数据窗口（cycleYearSpan 从 rows 的日期取年份，绝不用 new Date()）：
    // 面板开着时，月份列表与「N 个交易日」计数必须在同一次刷新里跟着这份窗口一起变 ——
    // 翻年、切区间、/cycle.json 刚到（renderCycle 末尾会调本函数）都走这一条路径
    if (datePop.classList.contains('open')) renderDatePopList();
  }

  // 事件委托：翻页按钮现在只在「选日期」面板首行（datePop 内），命中判定只需要认这一个容器
  function onCyclePageClick(event) {
    const target = event.target;
    const button = target && target.closest ? target.closest('[data-cycle-page]') : null;
    if (!button) return;
    if (!datePop.contains(button)) return;
    event.preventDefault();
    event.stopPropagation();
    if (button.disabled) return;
    applyCyclePage(button.getAttribute('data-cycle-page'));
    // 翻年同理：选中日不在新铺开的那一年里就清掉，日期键 / 梯队列 / 候选区永远同一天
    syncCycleDayAfterWindow();
  }

  function cycleNum(value) {
    // 注意 null：Number(null) === 0，会把「缺失」伪装成真实数字，必须先挡掉
    if (value === null || value === undefined || value === '') return null;
    const num = Number(value);
    return Number.isFinite(num) ? num : null;
  }
  function cycleShortDate(value) {
    const digits = String(value == null ? '' : value).replace(/\D/g, '');
    return digits.length >= 8 ? `${digits.slice(4, 6)}-${digits.slice(6, 8)}` : (digits || '—');
  }
  function cycleFixed(value, digits) {
    const num = cycleNum(value);
    return num == null ? '—' : num.toFixed(digits);
  }
  function cycleEmpty(text) {
    return `<div class="lt-cycle-empty">${esc(text || '数据待接入')}</div>`;
  }

  function renderCycleIndices(list, indicesDate) {
    const items = (Array.isArray(list) ? list : []).filter((item) => item && typeof item === 'object');
    if (!items.length) return cycleEmpty('指数数据待接入');
    // 本地通达信兜底时点位停在前一交易日收盘（不是基准日尾盘），所以把 bar 日期显式标在标题上；
    // 实时源不提供 bar 日期（indices_date 为 null/缺失）→ 只显示「指数」，绝不编日期
    const digits = String(indicesDate == null ? '' : indicesDate).replace(/\D/g, '');
    const label = digits.length >= 8 ? `指数（${cycleShortDate(digits)} 收盘）` : '指数';
    const cards = items.slice(0, 10).map((item) => {
      const pct = cycleNum(item.pct);
      const tone = pct == null ? 'lt-tone-flat' : pct > 0 ? 'lt-tone-up' : pct < 0 ? 'lt-tone-down' : 'lt-tone-flat';
      const pctText = pct == null ? '—' : `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`;
      const point = cycleNum(item.point);
      return `<div class="lt-cycle-idx"><div class="lt-cycle-idx-name">${esc(item.name || '—')}</div>`
        + `<div class="lt-cycle-idx-pct ${tone}">${pctText}</div>`
        + `<div class="lt-cycle-idx-point">${point == null ? '—' : point.toFixed(2)}</div></div>`;
    }).join('');
    return `<div class="lt-cycle-part"><div class="lt-cycle-label">${esc(label)}</div><div class="lt-cycle-indices">${cards}</div></div>`;
  }

  function renderCycleAmount(amount) {
    if (!amount || typeof amount !== 'object') return cycleEmpty('成交额数据待接入');
    const total = cycleNum(amount.total);
    const prev = cycleNum(amount.prev);
    const pct = cycleNum(amount.pct);
    if (total == null && prev == null && pct == null) return cycleEmpty('成交额数据待接入');
    const tone = pct == null ? 'lt-tone-flat' : pct > 0 ? 'lt-tone-up' : pct < 0 ? 'lt-tone-down' : 'lt-tone-flat';
    const arrow = pct == null ? '' : pct > 0 ? '▲' : pct < 0 ? '▼' : '·';
    const pctText = pct == null ? '—' : `${arrow}${Math.abs(pct).toFixed(1)}%`;
    return `<div class="lt-cycle-part"><div class="lt-cycle-label">成交额</div><div class="lt-cycle-amount">`
      + `今日 <b>${cycleFixed(total, 1)}</b> 亿 <span class="lt-cycle-temp-sep">|</span> `
      + `上一日 <b>${cycleFixed(prev, 1)}</b> 亿 <span class="lt-cycle-temp-sep">|</span> `
      + `变化 <b class="${tone}">${pctText}</b></div></div>`;
  }

  function renderCycleTemperature(temperature, breadth) {
    const temp = cycleNum(temperature);
    const up = cycleNum(breadth && breadth.up);
    const down = cycleNum(breadth && breadth.down);
    if (temp == null && up == null && down == null) return cycleEmpty('情绪温度数据待接入');
    // 档位只是温度的定性归类，不引入新数字
    const band = temp == null ? '' : temp < 20 ? '冰点' : temp < 40 ? '低温' : temp < 60 ? '常温' : temp < 80 ? '活跃' : '过热';
    const upHtml = up == null ? '—' : `<b class="lt-tone-up">${up}</b>`;
    const downHtml = down == null ? '—' : `<b class="lt-tone-down">${down}</b>`;
    return `<div class="lt-cycle-part"><div class="lt-cycle-label">情绪温度</div><div class="lt-cycle-temp">`
      + `<span class="lt-cycle-temp-val">${temp == null ? '—' : temp}</span>`
      + (band ? `<span>${band}</span>` : '')
      + `<span class="lt-cycle-temp-sep">|</span><span>涨跌家数 ${upHtml} / ${downHtml}</span>`
      + `</div></div>`;
  }

  // 情绪波浪图与连板梯队共用同一套列网格：N 列 × COLW 宽、column-gap 固定、
  // 左右留白一致，所以第 i 个 x 轴标签恒压在第 i 张卡片的中线上（面板变宽变窄都不错位）
  const COLW = 88;
  const GAP = 8;
  const PADL = 30;
  const PADR = 42;
  // 安全上限：区间裁剪（cycleRangeRows）之后仍按此兜底 —— 所选区间超过 260 列时取该区间最后 260 列。
  // 之前限 120 会把折线与梯队截到 120 天前，所以这里只防脏数据、不再当作实际窗口
  const MAXCOLS = 260;

  // ===== 显示区间裁剪（纯函数，无 DOM 依赖，可正则抽出单测）=====
  // 「当前自然年」只看 latestDate（= series 最后一条的 date）前四位，绝不用 new Date()；
  // latestDate 缺省/非法时回落到 rows 末条的 date，再取不到就原样返回（不裁剪）。
  //   two  = 上一自然年 + 当前自然年 全量（两个自然年）
  //   ytd  = 当前自然年年初至今
  //   last = 上一自然年全年
  // 未知/非法 mode 一律回落 CYCLE_RANGE_DEFAULT（ytd）；series 与 ladder 都走这一个函数，保证同源
  function cycleRangeRows(rows, mode, latestDate) {
    const list = (Array.isArray(rows) ? rows : []).filter((row) => row && typeof row === 'object');
    const pick = CYCLE_RANGE_KEYS.indexOf(mode) >= 0 ? mode : CYCLE_RANGE_DEFAULT;
    if (!list.length) return list;
    const stampOf = (value) => String(value == null ? '' : value).replace(/\D/g, '');
    const lastStamp = stampOf(latestDate) || stampOf(list[list.length - 1].date);
    const currentYear = Number(lastStamp.slice(0, 4));
    if (!Number.isFinite(currentYear) || currentYear <= 0) return list;
    const years = pick === 'two' ? [currentYear - 1, currentYear]
      : pick === 'last' ? [currentYear - 1] : [currentYear];
    return list.filter((row) => years.indexOf(Number(stampOf(row.date).slice(0, 4))) >= 0);
  }

  // 按固定自然年取全量 rows：年份口径与 cycleRangeRows 完全一致（date 前四位），
  // year 非正整数或 rows 不是数组时一律返回空数组（空即「该年无数据」，翻页据此禁用/跳过）。
  // series 与 ladder 必须走这同一个函数，保证两个 Map 的键集合永远一致，绝不混年份
  function cycleRowsOfYear(rows, year) {
    const target = Number(year);
    if (!Array.isArray(rows) || !Number.isInteger(target) || target <= 0) return [];
    return rows.filter((row) => row && typeof row === 'object'
      && Number(String(row.date).replace(/\D/g, '').slice(0, 4)) === target);
  }

  // 统一时间窗口：先按显示区间裁出同一份 rows（series 与 ladder 用同一个纯函数），再取末尾 MAXCOLS 列兜底。
  // 所有同源曲线（情绪波浪、压力线、涨跌停家数、两条预警线）与连板梯队列都只认这一份 dates，绝不混年份。
  // 第三参 pageYear = 固定显示的自然年（翻页用）：不从形参取而走 arguments，是因为
  // verify_cycle_range.js 用 /function cycleWindow\(data, mode\) \{/ 从源码切这段函数体做纯函数回归，
  // 签名多写一个参数就抽不到、那个脚本会直接抛错；语义上它就是第三参，省略/非正整数时走 cycleRangeRows，
  // 与加翻页之前逐字节等价（dates 仍按 MAXCOLS 兜底，一年 ≤243 天不会被截；ladderTotal 仍报该年全量）
  function cycleWindow(data, mode) {
    const pageYear = arguments.length > 2 ? arguments[2] : null;
    const page = Number(pageYear);
    const fixedYear = Number.isInteger(page) && page > 0 ? page : null;
    const source = data && typeof data === 'object' ? data : {};
    const seriesAll = (Array.isArray(source.series) ? source.series : []).filter((row) => row && typeof row === 'object');
    const ladderAll = (Array.isArray(source.ladder) ? source.ladder : []).filter((col) => col && typeof col === 'object');
    const latestDate = seriesAll.length ? seriesAll[seriesAll.length - 1].date : '';
    const seriesRows = fixedYear == null
      ? cycleRangeRows(seriesAll, mode, latestDate)
      : cycleRowsOfYear(seriesAll, fixedYear);
    const ladderRows = fixedYear == null
      ? cycleRangeRows(ladderAll, mode, latestDate)
      : cycleRowsOfYear(ladderAll, fixedYear);
    const total = Math.min(MAXCOLS, seriesRows.length);
    const dates = total ? seriesRows.slice(-total).map((row) => String(row.date || '')) : [];
    const byDate = new Map();
    seriesRows.forEach((row) => byDate.set(String(row.date || ''), row));
    const ladderByDate = new Map();
    ladderRows.forEach((col) => ladderByDate.set(String(col.date || ''), col));
    return { dates, series: byDate, ladder: ladderByDate, ladderTotal: ladderRows.length };
  }
  // ===== 选中某一天：候选区切成那天的历史快照 =====
  // 日期键统一规整成 8 位（'2025-03-14' / '20250314' 都认）；不像日期就返回 ''，调用方据此当成「没有」
  function cycleDayKey(value) {
    const digits = String(value == null ? '' : value).replace(/\D/g, '');
    return digits.length === 8 ? digits : '';
  }

  // 从整份 /cycle.json 的 rows（series / ladder 通用）里按日期取那一天的行。
  // 找不到 / 数据缺失 / 传进来的不是数组 → null（调用方显示「该日无梯队数据」，绝不抛错）
  function cycleRowOfDay(rows, date) {
    const key = cycleDayKey(date);
    if (!key || !Array.isArray(rows)) return null;
    for (let index = 0; index < rows.length; index += 1) {
      const row = rows[index];
      if (row && typeof row === 'object' && cycleDayKey(row.date) === key) return row;
    }
    return null;
  }

  // 某一天的连板梯队 / 那天的 series 统计行（历史候选的唯一数据源：不重算、不新开后端接口）
  function cycleDayLadder(data, date) {
    return cycleRowOfDay(data && typeof data === 'object' ? data.ladder : null, date);
  }
  function cycleDaySeries(data, date) {
    return cycleRowOfDay(data && typeof data === 'object' ? data.series : null, date);
  }

  // 快照顶部状态条：说清「正在看哪一天、不是最新」，并给一个一键回到最新的出口。
  // 这段只进下方候选区（body），不进图表、不进日期键行 —— 日期键行是列网格，多塞一个元素就错列
  function cycleDayBarMarkup(date) {
    const key = cycleDayKey(date);
    if (!key) return '';
    return '<div class="lt-day-bar"><b class="lt-day-bar-t">正在看 ' + esc(displayDate(key))
      + ' 的梯队（非最新）</b>'
      + '<button type="button" class="lt-btn lt-day-back" data-cycle-day-back="1"'
      + ' title="回到最新交易日的候选">回到最新</button>'
      + '<span class="lt-day-bar-hint">点情绪波浪里同一天再点一次也能取消</span></div>';
  }

  // 下方候选区在「选中某一天」时的完整内容：状态条 + 那天的连板梯队表。
  // 全部字段都直接取 data.ladder[那天]，不做任何推算；那天没有梯队就只留状态条 + 「该日无梯队数据」，
  // 用户点「回到最新」就能回去，绝不把面板卡死在这天
  function cycleDaySnapshotMarkup(data, date) {
    const key = cycleDayKey(date);
    if (!key) return '';
    const bar = cycleDayBarMarkup(key);
    const col = cycleDayLadder(data, key);
    if (!col) {
      return bar + '<div class="lt-empty"><b>该日无梯队数据</b>'
        + '<span>这一天在 /cycle.json 里没有连板梯队记录，换一天看看，或点「回到最新」。</span></div>';
    }
    const levels = (Array.isArray(col.levels) ? col.levels : [])
      .filter((level) => level && typeof level === 'object')
      .slice()
      .sort((left, right) => (cycleNum(right.boards) || 0) - (cycleNum(left.boards) || 0));
    const seriesRow = cycleDaySeries(data, key) || {};
    const maxHeight = col.max_height != null ? cycleNum(col.max_height) : null;
    const limitUp = cycleNum(seriesRow.limit_up);
    const limitDown = cycleNum(seriesRow.limit_down);
    const meta = '最高板 ' + (maxHeight == null ? '—' : maxHeight + ' 板')
      + ' · 涨停 ' + (limitUp == null ? '—（当日数据未更新）' : limitUp + ' 家')
      + ' · 跌停 ' + (limitDown == null ? '—（当日数据未更新）' : limitDown + ' 家');
    const rows = levels.map((level) => {
      const boards = cycleNum(level.boards);
      const names = (Array.isArray(level.stocks) ? level.stocks : [])
        .filter((stock) => stock && typeof stock === 'object')
        .map((stock) => esc(String(stock.name || stock.code || '—'))
          + (stock.code ? '<i class="lt-day-code">' + esc(String(stock.code)) + '</i>' : ''))
        .join('、');
      return '<tr><td class="lt-day-boards">' + (boards == null ? '—' : boards + '板') + '</td>'
        + '<td>' + (names || '无') + '</td></tr>';
    }).join('');
    const table = rows
      ? '<table class="lt-table lt-day-table"><thead><tr><th>身位</th><th>个股（该日连板梯队）</th></tr></thead>'
        + '<tbody>' + rows + '</tbody></table>'
      : '<div class="lt-empty"><b>该日无连板梯队</b><span>这一天没有 2 板及以上的连板股记录。</span></div>';
    return bar + '<div class="lt-day-meta">' + esc(meta) + '</div>' + table;
  }

  // ===== 选中某一天：下方候选区显示那一天的日报（当日 09:25 竞价口径的候选表）=====
  // 动机：用户构建回测，要看的是「那一天 09:25 竞价条件下算出来的候选排名」，
  // 光有「那天有哪些票连板」的梯队快照回答不了这个问题。日报正文就在
  // output/reports/<YYYYMMDD>.md，服务端 /report.md?date=YYYYMMDD 原样返回，前端继续用
  // renderMarkdown 解析 —— 阶段排序、跨表列宽同步、「竞额20日比%」排序箭头全部复用同一套。
  // 缓存：日期 → { text } / { missing: true }（已确认那天没有日报，不重复请求）
  const cycleDayReportCache = new Map();
  // 竞态保护：连续点不同日子、或中途回到最新时，只认最后一次要求的响应
  let cycleDayReportSeq = 0;

  // 有日报时的状态条：说清看的是哪一天的候选、口径是当日 09:25 竞价、不是最新
  function cycleDayReportBarMarkup(date) {
    const key = cycleDayKey(date);
    if (!key) return '';
    return '<div class="lt-day-bar"><b class="lt-day-bar-t">正在看 ' + esc(displayDate(key))
      + ' 的候选（当日 09:25 竞价口径，非最新）</b>'
      + '<button type="button" class="lt-btn lt-day-back" data-cycle-day-back="1"'
      + ' title="回到最新交易日的候选">回到最新</button>'
      + '<span class="lt-day-bar-hint">点情绪波浪里同一天再点一次也能取消</span></div>';
  }

  // 取不到日报时的状态条（2025 年及更早还没有日报文件）：如实说清下面是回落出来的梯队快照
  function cycleDayMissingBarMarkup(date) {
    const key = cycleDayKey(date);
    if (!key) return '';
    return '<div class="lt-day-bar"><b class="lt-day-bar-t">该日无日报（日报自 2026-01-05 起才有），'
      + '下面是该日梯队快照</b>'
      + '<button type="button" class="lt-btn lt-day-back" data-cycle-day-back="1"'
      + ' title="回到最新交易日的候选">回到最新</button>'
      + '<span class="lt-day-bar-hint">点情绪波浪里同一天再点一次也能取消</span></div>';
  }

  // 取某一天的日报正文：命中缓存直接返回；404 = 确认没有（缓存 missing）；
  // 其它失败（断网 / 500 / 非空但取不到）不缓存，下次进来还能重试
  async function fetchCycleDayReport(date) {
    const key = cycleDayKey(date);
    if (!key) return { missing: true };
    const cached = cycleDayReportCache.get(key);
    if (cached) return cached;
    let result = null;
    try {
      const response = await fetch('/report.md?date=' + encodeURIComponent(key), {
        credentials: 'same-origin',
        cache: 'no-store',
      });
      if (response.ok) {
        const text = await response.text();
        // 200 但正文为空：当作没有日报，同样不缓存（下次重试）
        if (String(text || '').trim()) result = { text };
      } else if (response.status === 404) {
        result = { missing: true };
      }
    } catch (error) {
      result = null; // 网络异常：不缓存失败，下次进来还能重试
    }
    // 上限：一天一份整份日报，最多留 40 天，超出整批清空，避免长开越积越大。
    if (result && cycleDayReportCache.size > 40) cycleDayReportCache.clear();
    if (result) cycleDayReportCache.set(key, result);
    return result || { missing: true };
  }

  // 日报正文的渲染：与 renderCurrent 走同一套管线（先摘掉机器可读的情绪/事件明细再 renderMarkdown），
  // 保证「个股情绪」「事件驱动」两列的提示与最新态完全一致
  function cycleDayReportMarkup(markdown) {
    const extracted = extractEmotionDetails(markdown);
    const extractedEvent = extractEventDetails(extracted.markdown);
    return renderMarkdown(extractedEvent.markdown, extracted.details, extractedEvent.details);
  }

  // 切的那一天是否还在当前图表窗口里：切区间 / 翻年之后如果那天被裁掉了，
  // 必须把选中日清掉并重绘候选区 —— 绝不允许「图表铺的是 2026，候选还停在 2025」
  function syncCycleDayAfterWindow() {
    if (cycleDayDate == null) return;
    const data = cycleLastData && typeof cycleLastData === 'object' ? cycleLastData : cycleData;
    if (!data || typeof data !== 'object') {
      cycleDayDate = null;
      renderCurrent();
      return;
    }
    const win = cyclePageYear == null
      ? cycleWindow(data, cycleRangeMode)
      : cycleWindow(data, cycleRangeMode, cyclePageYear);
    const key = cycleDayKey(cycleDayDate);
    const dates = win && Array.isArray(win.dates) ? win.dates : [];
    if (dates.some((date) => cycleDayKey(date) === key)) return;
    cycleDayDate = null;
    renderCurrent();
  }

  // 断板反包叠加层：把「断板前是当日最高层 N 板（N>=4）→ 次日断板 → 之后连续涨停 k 天」
  // 换算成「断板后第 k 天」那一列的一行叠加标记（N+k板 + 名称），再次断板即自然停止。
  // 入参传 ladder 全量行（不是窗口行）：跨年/翻页时窗口左边界缺的前序历史也能参与判定，缺多少算多少。
  // 返回 Map<date, [{code,name,N,k,label}]>；同日多条按 N+k 倒序。
  // 断板反包叠加的只读自检快照（?probe=overlay 读取），显式声明避免隐式全局
  let cycleOverlayProbe = { cols: [] };

  function cycleOverlayByDate(ladderRows) {
    const rows = (Array.isArray(ladderRows) ? ladderRows : [])
      .filter((col) => col && typeof col === 'object')
      .map((col) => {
        const levels = (Array.isArray(col.levels) ? col.levels : []).filter((level) => level && typeof level === 'object');
        const byCode = new Map();
        let maxBoards = null;
        levels.forEach((level) => {
          const boards = cycleNum(level.boards);
          if (boards == null) return;
          if (maxBoards == null || boards > maxBoards) maxBoards = boards;
          (Array.isArray(level.stocks) ? level.stocks : []).forEach((stock) => {
            if (!stock || typeof stock !== 'object') return;
            const code = String(stock.code || '');
            if (!code || byCode.has(code)) return;
            byCode.set(code, { code, name: String(stock.name || code), boards });
          });
        });
        // N 取层级里真实股票的最高 boards：declared max_height 可能带「停牌高度」但没有对应层级股，会漏掉整段周期
        return { date: String(col.date || ''), top: maxBoards, byCode };
      })
      .filter((day) => day.date);
    rows.sort((left, right) => (left.date < right.date ? -1 : (left.date > right.date ? 1 : 0)));
    const overlayByDate = new Map();
    rows.forEach((day, index) => {
      if (index === 0) return;
      const prev = rows[index - 1];
      // 断板前一日必须是当日最高层且 N>=4
      if (prev.top == null || prev.top < 4) return;
      // 当日缺数据（无层级也无 max_height）：不确认断板、不推进 k，等下一交易日再判定
      if (day.top == null) return;
      // 断板确认：该股当日不在梯队任意层，且当日最高板被压低（说明高度已断）
      if (day.top >= prev.top) return;
      prev.byCode.forEach((stock) => {
        if (stock.boards !== prev.top || day.byCode.has(stock.code)) return;
        // 断板后连续涨停：第 step 天梯队里 boards 必须恰好等于 step，中断即停
        for (let step = 1; index + step < rows.length; step += 1) {
          const next = rows[index + step];
          const hit = next.byCode.get(stock.code);
          if (!hit || hit.boards !== step) break;
          if (!overlayByDate.has(next.date)) overlayByDate.set(next.date, []);
          overlayByDate.get(next.date).push({
            code: stock.code,
            name: stock.name,
            N: prev.top,
            k: step,
            label: `${prev.top}+${step}板`,
          });
        }
      });
    });
    overlayByDate.forEach((list) => list.sort((left, right) => (right.N + right.k) - (left.N + left.k)));
    return overlayByDate;
  }

  function renderCycleWave(data) {
    cycleLastData = data; // 数据缓存：拖动量程时用同一份数据实时重绘（见 redrawCycleWave）
    // 区间档 + 固定翻页年：翻页年非空时整窗换成该自然年（null 时与只按区间档裁剪逐字节等价）
    const win = cyclePageYear == null
      ? cycleWindow(data, cycleRangeMode)
      : cycleWindow(data, cycleRangeMode, cyclePageYear);
    if (!win.dates.length) {
      cycleOverlayProbe = { cols: [] };
      return cycleEmpty('走势数据待接入');
    }
    // 断板反包叠加：源数据用 ladder 全量（不是 win.ladder，窗口裁过的会丢掉断板前的最高板信息）
    const overlayByDate = cycleOverlayByDate(data && data.ladder);
    // 只读自检快照：?probe=overlay 用，覆盖当前窗口每一列（无叠加的日期给空数组）
    cycleOverlayProbe = {
      cols: win.dates.map((date) => ({
        date,
        overlays: (overlayByDate.get(date) || []).map((item) => ({
          code: item.code, name: item.name, N: item.N, k: item.k, label: item.label,
        })),
      })),
    };
    const n = win.dates.length;
    const width = PADL + n * COLW + (n - 1) * GAP + PADR;
    const height = 205;
    const padL = PADL;
    const padR = PADR;
    const padT = 18;
    const padB = 26;
    const innerH = height - padT - padB;
    const baseY = padT + innerH;
    const nodes = win.dates.map((date) => {
      const row = win.series.get(date) || {};
      return {
        date: cycleShortDate(date),
        height: cycleNum(row.max_height),
        heightEx: cycleNum(row.height_ex),
        // 停牌高度：当日停牌股（高度取停牌前连板数）的最高值，null 表示当日无停牌股
        suspended: cycleNum(row.suspended_height),
        up: cycleNum(row.up),
        down: cycleNum(row.down),
        // 全市场总体涨停/跌停家数（不是梯队内数量），家数两线的数据源
        limitUp: cycleNum(row.limit_up),
        limitDown: cycleNum(row.limit_down),
        // 全市场成交额（亿元）：右轴独立量程的「成交额」黄线数据源；缺值当天不产点（断线，绝不拿 0 顶替）
        amount: cycleNum(row.amount),
      };
    });
    const xOf = (index) => padL + index * (COLW + GAP) + COLW / 2;
    // ===== 压力板 C 方案：压力位 P(d) = max(当日最高板 H(d), 前一日最高板 H(d-1)) =====
    // 窗口首日只取当日 H（没有前一日就不并入），停牌高度一律不参与 → 停牌当日不下移。
    // 按「平台段」记录：压力位上升当日旧线在 i-1 终止、当日另起新段（断线、旧线不向后延伸）；
    // 持平/回落则延续到当日。只有 1 个点的段在画图时用同色小圆点表示，避免该点消失。
    const pressure = nodes.map(() => null);
    const pressurePlatforms = [];
    for (let i = 0; i < nodes.length; i += 1) {
      const h = nodes[i].height;
      const prev = i > 0 ? nodes[i - 1].height : null;
      const values = [h, prev].filter((value) => value != null);
      const level = values.length ? Math.max(...values) : null;
      pressure[i] = level;
      if (level == null) continue;
      const last = pressurePlatforms[pressurePlatforms.length - 1];
      const prevLevel = i > 0 ? pressure[i - 1] : null;
      const brokeThrough = prevLevel != null && level > prevLevel;
      if (last && !brokeThrough && last.end === i - 1) { last.end = i; last.points.push(i); }
      else { pressurePlatforms.push({ level, start: i, end: i, points: [i] }); }
    }
    // 左轴域：随当日实际最大高度自适应（下限 10，见 cycleHeightDomainMax），红线/紫线/压力板共用一个域。
    // 压力位也纳入域：P 取当日与前一日最高板的较高者，可能高过当日 max_height，必须一起算进 domainMax 兜底
    const heightValues = nodes.reduce((acc, node) => {
      if (node.height != null) acc.push(node.height);
      if (node.heightEx != null) acc.push(node.heightEx);
      return acc;
    }, []);
    pressure.forEach((value) => {
      if (value != null) heightValues.push(value);
    });
    // 左轴量程：autoDomain = 数据算出的自动上界（下限 10，见 cycleHeightDomainMax），
    // 有拖动覆盖值时用覆盖值，但夹在 [4, autoDomain] ——
    //   下界 4 板：3 板线固定在 y3，域顶至少留到 4 板，否则曲线挤成一条；
    //   上界 autoDomain：往下拖最多回到自动量程，不放大到超出窗口实际极值。
    // 只换这一个变量，红折线/紫线/压力板/柱/刻度/图例就一起跟着新量程动；右轴家数轴（yCount）不受影响。
    const autoDomain = cycleHeightDomainMax(heightValues);
    cycleLastAutoDomain = autoDomain;
    const domainMax = cycleHeightDomainOverride == null
      ? autoDomain
      : Math.min(autoDomain, Math.max(CYCLE_HEIGHT_DRAG_MIN_DOMAIN, cycleHeightDomainOverride));
    // 左轴：连板高度（临界点固定 + 超限压缩，见 cycleHeightY）；右轴：涨跌家数 0–150
    const yHeight = (value) => cycleHeightY(value, domainMax, padT, innerH);
    const yBreadth = (value) => padT + innerH * (1 - Math.max(0, Math.min(150, value == null ? 0 : value)) / 150);
    // ===== 家数独立纵轴：0 家固定 baseY、100 家固定 y100、>100 家压进 22px 带；与板数轴/涨跌家数轴都不共轴 =====
    const yCount = (value) => cycleCountY(value, padT, innerH);
    // ===== 成交额「独立右轴」（单位亿元）：与左轴板数、家数轴 yCount 都不共轴 =====
    // 量级是千亿~万亿级，跟 0-10 板的左轴差 3 个数量级，所以单开一条轴：0 贴 baseY、峰值贴 padT。
    // 上界按窗口内最大成交额向上取到 500 亿的整档（amountTop），刻度只用 0 / 半程 / 顶格三个，
    // 与左轴刻度同一套文字样式（.lt-cv-tick），不另起一套排版
    const amountValues = nodes.map((node) => node.amount).filter((value) => value != null);
    const amountPeak = amountValues.length ? Math.max(...amountValues) : 0;
    const amountTop = amountPeak > 0 ? Math.ceil(amountPeak / 500) * 500 : 500;
    const yAmount = (value) => padT + innerH * (1 - Math.max(0, Math.min(amountTop, value == null ? 0 : value)) / amountTop);
    // 成交额线的点坐标：缺值当天不产点（断线），与家数两线同一规则
    const amountPoints = nodes
      .map((node, index) => (node.amount == null ? null : `${xOf(index).toFixed(1)},${yAmount(node.amount).toFixed(1)}`))
      .filter(Boolean);
    const barW = Math.max(4, Math.min(10, COLW * 0.26));
    // 紫线数值标签：与紫线点一一对应（height_ex 为 null 的点不产点、也不产标签），
    // 坐标和数值一起写进 data-maxboard-labels，供开关勾选时原样恢复，不重算
    const exLabels = nodes
      .map((node, index) => (node.heightEx == null ? null : { x: xOf(index), y: yHeight(node.heightEx), value: node.heightEx }))
      .filter(Boolean);
    const exLabelMarkup = exLabels
      .map((item) => `<text class="lt-cv-val-ex" x="${item.x.toFixed(1)}" y="${(item.y + 15).toFixed(1)}" text-anchor="middle">${item.value}</text>`)
      .join('');
    const exLabelAttr = exLabels.map((item) => `${item.x.toFixed(1)},${item.y.toFixed(1)},${item.value}`).join(';');
    // 紫色「剔除一字后最高板」的点坐标：数据源是 series[*].height_ex（当日最高板里剔除一字板后的层），
    // 与红线独立但共用同一自适应 domain，不复用红线 DOM、也不因开关状态重算 → 勾选/取消时其它线的坐标恒定
    // height_ex 为 null 的日期直接不产点（连线断开），绝不拿 max_height 顶替
    const maxboardPoints = nodes
      .map((node, index) => (node.heightEx == null ? null : `${xOf(index).toFixed(1)},${yHeight(node.heightEx).toFixed(1)}`))
      .filter(Boolean);
    // 家数两线的点坐标：y 全部由家数轴算；缺值当天不产点（断线，绝不拿 0 顶替）
    const countUpPoints = nodes
      .map((node, index) => (node.limitUp == null ? null : `${xOf(index).toFixed(1)},${yCount(node.limitUp).toFixed(1)}`))
      .filter(Boolean);
    const countDownPoints = nodes
      .map((node, index) => (node.limitDown == null ? null : `${xOf(index).toFixed(1)},${yCount(node.limitDown).toFixed(1)}`))
      .filter(Boolean);
    // 两条横贯虚线的 y：高潮预警线固定在「家数轴 100 家」的固定像素位 y100（= padT + innerH×0.06），
    // 不再随窗口峰值浮动；冰点试错线固定「高度轴 3 板」的固定像素位 y3（= baseY - innerH×0.16）
    const countY100 = padT + innerH * CYCLE_COUNT_Y100_RATIO;
    const heightY3 = baseY - innerH * CYCLE_HEIGHT_Y3_RATIO;
    const overheatY = countY100;
    const freezeY = heightY3;
    // 压力位折线按「平台段」输出：points>1 的段拼 M/L，孤立单点段不连线（该点由下面的圆+数字表示，
    // 不会消失）；段与段之间不连 → 压力位上升日自然断线，旧线在 i-1 终止、不再向后延伸
    const pressureD = pressurePlatforms
      .filter((platform) => platform.points.length > 1)
      .map((platform) => platform.points
        .map((index, order) => `${order === 0 ? 'M' : 'L'}${xOf(index).toFixed(1)},${yHeight(pressure[index]).toFixed(1)}`)
        .join(''))
      .join('');
    // 孤立单点段坐标（分号连接）：写进 svg 的 data-pressure-dots 备查；
    // 实际图形由 nodes 的圆+数字覆盖，所以不再单独画小圆点
    const pressureDots = pressurePlatforms
      .filter((platform) => platform.points.length === 1)
      .map((platform) => {
        const index = platform.points[0];
        return `${xOf(index).toFixed(1)},${yHeight(pressure[index]).toFixed(1)}`;
      });
    // 压力板节点：每天一个同色圆 + 圆内数字，坐标与折线同源；突破日、孤立单点也不漏标
    const pressureNodeAttr = nodes
      .map((node, index) => {
        const value = pressure[index];
        if (value == null) return null;
        return `${xOf(index).toFixed(1)},${yHeight(value).toFixed(1)},${value}`;
      })
      .filter(Boolean)
      .join(';');
    const parts = [`<svg class="lt-cv" viewBox="0 0 ${width} ${height}" style="width:${width}px;height:${height}px;--lt-cv-pressure-w:${CYCLE_PRESSURE_STROKE};--lt-cv-count-w:${CYCLE_COUNT_STROKE}" data-cols="${n}" data-padl="${padL}" data-colw="${COLW}" data-gap="${GAP}" data-padt="${padT}" data-basey="${baseY}" data-maxboard-points="${maxboardPoints.join(' ')}" data-maxboard-labels="${exLabelAttr}" data-pressure-path="${pressureD}" data-pressure-dots="${pressureDots.join(';')}" data-pressure-nodes="${pressureNodeAttr}" role="img" aria-label="情绪周期走势：红线为每日最高板、紫线为剔除一字后最高板、黄虚线为压力板（当日最高板与前一日最高板取高），两条粗浅色线为全市场涨停/跌停家数（独立家数轴），柱为涨跌家数，黄线为全市场成交额（右轴，亿元）">`];

    // 左轴刻度跟着自适应域走：原点、半程（取整）、顶端，全部用新的非线性映射重算，与图例 0-domainMax 一致
    Array.from(new Set([0, Math.round(domainMax / 2), domainMax])).forEach((tick) => {
      const y = yHeight(tick);
      parts.push(`<line class="lt-cv-grid" x1="${padL}" y1="${y.toFixed(1)}" x2="${width - padR}" y2="${y.toFixed(1)}"/>`);
      parts.push(`<text class="lt-cv-tick" x="${padL - 5}" y="${(y + 3).toFixed(1)}" text-anchor="end">${tick}</text>`);
    });
    // 右轴刻度改成成交额（亿元）：原来的 0/75/150 是已删除的「涨跌家数红绿柱」用的 yBreadth 刻度，
    // 柱子删掉后这组刻度不对应任何图形（家数两线走的是 yCount 非线性映射），这里换成成交额刻度
    [0, Math.round(amountTop / 2), amountTop].forEach((tick) => {
      const y = yAmount(tick);
      parts.push(`<text class="lt-cv-tick" x="${width - padR + 5}" y="${(y + 3).toFixed(1)}" text-anchor="start">${tick}</text>`);
    });

    // 涨跌家数红绿竖柱已按用户要求删除（2026-11-08）：历史 203 天里只有最新日 20261106 带
    // up/down 字段，所以图里只在最右一列冒出两根孤立的红绿柱，看着像脏点。这里是删掉的整段
    // 原实现，数据（series[].up / down）与右轴 yBreadth 映射都保留，需要恢复时把 forEach 放回来即可。
    //   nodes.forEach((node, index) => { ... <rect class="lt-cv-bar-up"> ... <rect class="lt-cv-bar-down"> ... });

    // 压力板：分段折线 + 每个压力点的同色圆与圆内数字（勾选才输出；group 始终存在，供开关重建）。
    // 放在红线之前 → 压力位与当日最高板同高时（P == H，数值本来就相同）不会盖住红点和红字，
    // 压力位高于红线的日子仍完整可见；开关只清空/重建这个 group，坐标不重算
    if (pressureD || pressureNodeAttr) {
      parts.push(`<g class="lt-cv-pressure-group">${cyclePressureOn ? cyclePressureMarkup(pressureD, pressureDots.join(';'), pressureNodeAttr) : ''}</g>`);
    }

    // ===== 家数两线 + 两条横贯虚线：放在所有现有折线之前 → 绘制顺序最前 = 视觉最底层 =====
    // 用户原意「重合层级放在最后」= 置后/垫底，不是盖在最上面，所以这里必须早于红线/紫线/压力板 push；
    // 线宽 = 压力线 × 3、半透明浅色（stroke-opacity .22），与红线重合时被红线覆盖；
    // 这两条粗线只是「当日全市场涨跌停家数」的背景参考，线上不写任何数字文本节点，
    // 具体数值只在点击当日列后的浮层里给（见 renderCycleLadderCard 的 data-tip + onCycleDayClick）
    if (countUpPoints.length > 1) {
      parts.push(`<polyline class="lt-cv-count lt-cv-count-up" points="${countUpPoints.join(' ')}"/>`);
    }
    if (countDownPoints.length > 1) {
      parts.push(`<polyline class="lt-cv-count lt-cv-count-down" points="${countDownPoints.join(' ')}"/>`);
    }
    // ===== 成交额黄线（右轴，亿元）：规格与上面两条家数线完全一致 —— 直接复用 .lt-cv-count 类
    // （线宽 var(--lt-cv-count-w)=4.5、stroke-opacity .22、实线、round 端点），只把颜色换成
    // .lt-cv-amount 的黄色 --lt-temp；同样不带任何数据点圆、不写数字文本；
    // 数值仍只进当日列点击浮层的 dayTip（与家数同一张卡片）
    // 画在家数线之后、压力板与红线之前 → 与红线重合时被红点/红字覆盖，可读性与家数线同一层级
    if (amountPoints.length > 1) {
      parts.push(`<polyline class="lt-cv-count lt-cv-amount" points="${amountPoints.join(' ')}"/>`);
    }
    // 高潮预警线（家数轴 100 家处，涨停 >100 家 = 警惕次日兑现）+ 冰点试错线（高度轴 3 板处，最高板 ≤3 板 = 极小仓位or空仓）
    if (overheatY != null) {
      parts.push(`<line class="lt-cv-overheat" x1="${padL}" y1="${overheatY.toFixed(1)}" x2="${width - padR}" y2="${overheatY.toFixed(1)}"/>`);
    }
    if (freezeY != null) {
      parts.push(`<line class="lt-cv-freeze" x1="${padL}" y1="${freezeY.toFixed(1)}" x2="${width - padR}" y2="${freezeY.toFixed(1)}"/>`);
    }

    // 红线：每日最高板，逐节点画同色实心圆（r=PRESSURE_NODE_R）并在圆内写白字；
    // 骤降 ≥2 板在圆下方 y+21 处标红色 ▼（给半径 8 的圆让位）
    const linePoints = nodes
      .map((node, index) => (node.height == null ? null : `${xOf(index).toFixed(1)},${yHeight(node.height).toFixed(1)}`))
      .filter(Boolean);
    if (linePoints.length > 1) parts.push(`<polyline class="lt-cv-line" points="${linePoints.join(' ')}"/>`);
    nodes.forEach((node, index) => {
      if (node.height == null) return;
      const x = xOf(index);
      const y = yHeight(node.height);
      // 红点=实心圆（填充 --lt-line、无描边），数字写进圆内垂直居中（y + PRESSURE_NODE_TY）
      parts.push(`<circle class="lt-cv-dot" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${PRESSURE_NODE_R}"/>`);
      parts.push(`<text class="lt-cv-val" x="${x.toFixed(1)}" y="${(y + PRESSURE_NODE_TY).toFixed(1)}" text-anchor="middle">${node.height}</text>`);
      const prevHeight = index > 0 ? nodes[index - 1].height : null;
      if (prevHeight != null && prevHeight - node.height >= 2) {
        parts.push(`<text class="lt-cv-drop" x="${x.toFixed(1)}" y="${(y + 21).toFixed(1)}" text-anchor="middle">▼</text>`);
      }
    });

    // 紫色「剔除一字后最高板」：独立数据源（series[*].height_ex），只有开关打开时才输出这条 polyline
    // 与它的数值标签；追加在最后 → 画在红线上层（虚线所以两条都可见）
    if (cycleMaxBoardOn && maxboardPoints.length > 1) {
      parts.push(`<polyline class="lt-cv-line-max" points="${maxboardPoints.join(' ')}"/>`);
    }
    // 标签跟线一起受同一个开关控制：默认不勾选时既不连线也不出数字
    if (cycleMaxBoardOn && exLabelMarkup) {
      parts.push(`<g class="lt-cv-ex-labels">${exLabelMarkup}</g>`);
    }

    parts.push('</svg>');
    const legend = '<div class="lt-cycle-legend">'
      + cycleRangeToggleMarkup(data)
      + `<span><i class="lt-lg-dot"></i>最高板（左轴 0-${domainMax}）</span>`
      + '<span><i class="lt-lg-purple"></i>剔除一字后最高板'
      + `<input type="checkbox" class="lt-cv-max-toggle"${cycleMaxBoardOn ? ' checked' : ''} aria-label="显示剔除一字后最高板折线"/></span>`
      + '<span><i class="lt-lg-bar-up"></i>涨家数</span>'
      + '<span><i class="lt-lg-bar-down"></i>跌家数</span>'
      + '<span><i class="lt-lg-pressure"></i>压力板（最高板当日即压力位，停牌当日不下移）'
      + `<input type="checkbox" class="lt-cv-pressure-toggle"${cyclePressureOn ? ' checked' : ''} aria-label="显示压力板折线"/></span>`
      + `<span><i class="lt-lg-count-up"></i>涨停家数（家数轴 100 家顶格，超限压缩，全市场）</span>`
      + '<span><i class="lt-lg-count-down"></i>跌停家数（同一家数轴）</span>'
      + `<span><i class="lt-lg-amount"></i>成交额（右轴 0-${amountTop} 亿）</span>`
      + '<span><i class="lt-lg-overheat"></i>高潮预警线（警惕次日兑现）· 涨停 &gt;100 家</span>'
      + '<span><i class="lt-lg-freeze"></i>冰点试错线（极小仓位or空仓）· 最高板 ≤3 板</span>'
      + '<span><i class="lt-lg-drop">▼</i>骤降≥2板</span>'
      + '</div>';
    // x 轴日期不再画进 SVG：改由 DOM 列网格渲染，与梯队卡片共用列宽/列距/左右留白
    const topLevel = win.dates.reduce((max, date) => {
      const col = win.ladder.get(date);
      const value = col ? cycleNum(col.max_height) : null;
      return value != null && value > max ? value : max;
    }, 0);
    const axis = win.dates.map((date) => `<span>${esc(cycleShortDate(date))}</span>`).join('');
    const cards = win.dates.map((date) => renderCycleLadderCard(win.ladder.get(date), date, topLevel, win.series.get(date), overlayByDate.get(date))).join('');
    const note = win.ladderTotal > win.dates.length
      ? `<div class="lt-cycle-none">梯队仅显示最近 ${win.dates.length} 个交易日</div>`
      : '';
    const trackStyle = `--lt-cv-cols:${win.dates.length};--lt-cv-colw:${COLW}px;--lt-cv-gap:${GAP}px;`
      + `--lt-cv-padl:${PADL}px;--lt-cv-padr:${PADR}px`;
    // 图例挪到图上方：紫色「当日最高板」默认不勾选，勾选框必须在图之前才一眼看见
    return `<div class="lt-cycle-part"><div class="lt-cycle-label">情绪波浪 · 最高板 / 涨跌家数（与连板梯队同轴）</div>`
      + `${legend}<div class="lt-cycle-scroll"><div class="lt-cycle-track" style="${trackStyle}">${parts.join('')}`
      + `<div class="lt-cycle-vp"><div class="lt-cycle-axis">${axis}</div>`
      + `<div class="lt-cycle-ladder">${cards}</div></div></div></div>${note}</div>`;
  }

  // 板数色阶（绝对板数）：2板→灰、3板→蓝、4板→深紫、5板及以上→红。
  // 现在只剩列头 .lt-cycle-col-h 在用；梯队各层已改为列内相对名次（ladderRankToneClass），
  // 以免各列的 5/6 板都染红、与列顶当日最高板撞色。保持原语义不动，别顺手改它
  function boardToneClass(boards) {
    const value = cycleNum(boards);
    if (value == null) return '';
    if (value >= 5) return ' lt-lv-t5';
    if (value === 4) return ' lt-lv-t4';
    if (value === 3) return ' lt-lv-t3';
    return ' lt-lv-t2';
  }

  // 天梯行色阶：按「列内相对名次」着色 —— 第1名红、第2名深紫、第3名蓝、第4名及更后灰。
  // 红只留给每列最高档，所以 9板/5板/4板/2板 = 红/紫/蓝/灰，6板在 10板 那列是紫而不是红。
  // 名次按「不同板数降序」编号，同一板数的多只股票共用同一个名次 → 同色，
  // 不受遍历顺序影响，也不会因为多一只股票把下面的档位整体挤低一级
  function ladderRankMap(levels) {
    const values = Array.from(new Set(
      (Array.isArray(levels) ? levels : [])
        .map((level) => cycleNum(level && level.boards))
        .filter((value) => value != null),
    )).sort((left, right) => right - left);
    return new Map(values.map((value, index) => [value, index + 1]));
  }

  function ladderRankToneClass(boards, rankMap) {
    const value = cycleNum(boards);
    const rank = value == null || !rankMap ? null : rankMap.get(value);
    if (rank === 1) return ' lt-lv-t5';
    if (rank === 2) return ' lt-lv-t4';
    if (rank === 3) return ' lt-lv-t3';
    return ' lt-lv-t2';
  }

  function renderCycleLadderCard(col, date, topLevel, seriesRow, overlays) {
    const levels = (col && Array.isArray(col.levels) ? col.levels : [])
      .filter((level) => level && typeof level === 'object')
      .slice()
      .sort((left, right) => (cycleNum(right.boards) || 0) - (cycleNum(left.boards) || 0));
    const declared = col ? cycleNum(col.max_height) : null;
    const maxHeight = declared != null ? declared : (levels.length ? cycleNum(levels[0].boards) : null);
    const hot = maxHeight != null && maxHeight === topLevel && topLevel > 0 ? ' lt-hot' : '';
    // 每列只算一次名次表，供该列所有层取色
    const rankMap = ladderRankMap(levels);
    // 当日列点击浮层的内容：涨停/跌停家数（全市场总体）+ 两条预警线的触发判定。
    // 数字只写进 data-tip（点击当日列后才由现成浮层显示），不进 SVG、也不常驻在列上
    const limitUp = cycleNum(seriesRow && seriesRow.limit_up);
    const limitDown = cycleNum(seriesRow && seriesRow.limit_down);
    // 缺失（null / 键不存在）统一显示「—（当日数据未更新）」，绝不显示成 0，也不会出现 NaN / undefined：
    // 历史日期的家数来自 market_stats；基准日靠实时补齐，没补上时这里就是缺失
    const countText = (value) => (value == null ? '—（当日数据未更新）' : String(cycleNum(value)));
    const dayTip = `涨停 ${countText(limitUp)} 家 / 跌停 ${countText(limitDown)} 家`
      + ' · ' + (limitUp == null
        ? '高潮预警：未判定（涨停家数未更新，预警线固定在家数轴 100 家）'
        : (limitUp > CYCLE_OVERHEAT_LIMIT
          ? `高潮预警：触发（涨停 ${limitUp} 家 >100 家，警惕次日兑现）`
          : `高潮预警：未触发（涨停 ${limitUp} 家 ≤100 家）`))
      + ' · ' + (maxHeight == null
        ? '冰点试错：未判定（最高板 —，当日数据未更新）'
        : (maxHeight <= CYCLE_FREEZE_BOARD
          ? `冰点试错：触发（最高板 ${maxHeight} 板 ≤3 板，极小仓位or空仓）`
          : `冰点试错：未触发（最高板 ${maxHeight} 板 >3 板）`));
    // 断板反包叠加行：紧跟最高板梯队之后插入（不改层级排序、不动列头最高板数字），再次断板自然消失
    const overlayRows = (Array.isArray(overlays) ? overlays : []).map((item) => {
      if (!item) return '';
      const label = String(item.label || `${item.N}+${item.k}板`);
      const name = String(item.name || item.code || '—');
      return `<div class="lt-cycle-lv lt-lv-overlay"><span class="lt-cycle-lv-b">${esc(label)}</span>`
        + `<span class="lt-cycle-stock" title="${esc(item.code || '')}">${esc(name)}</span></div>`;
    }).filter(Boolean).join('');
    const rows = levels.length
      ? levels.map((level, levelIndex) => {
        const boards = cycleNum(level.boards);
        const isTop = maxHeight != null && boards === maxHeight;
        // 身位配色：最高板保持原色（fade=1），往下同一色系逐级变淡；
        // 档数按当日实际层数动态算，不写死固定档位
        const step = levels.length > 1 ? levelIndex / (levels.length - 1) : 0;
        const fade = Math.round((1 - 0.45 * step) * 100) / 100;
        const tone = ladderRankToneClass(boards, rankMap);
        const stocks = (Array.isArray(level.stocks) ? level.stocks : []).map((stock) => {
          if (!stock) return '';
          const name = String(stock.name || stock.code || '—');
          // 竞价成交额只作为 data 属性带出来，真正的金额行由 decorateLadderAuction 用 DOM 插，
          // 不拼进 innerHTML（拼字符串会动到已有梯队方块布局）
          const auction = formatAuctionAmount(stock.auction_yi);
          const auctionAttr = auction ? ` data-auction-yi="${esc(stock.auction_yi)}"` : '';
          return `<span class="lt-cycle-stock${isTop ? ' lt-tone-up' : ''}" title="${esc(stock.code || '')}"${auctionAttr}>${esc(name)}</span>`;
        }).filter(Boolean).join('');
        const levelRow = `<div class="lt-cycle-lv${tone}" style="--lt-fade:${fade}"><span class="lt-cycle-lv-b">${boards == null ? '—' : boards}板</span>`
          + `${stocks || '<span class="lt-cycle-none">无</span>'}</div>`;
        // 叠加层紧跟最高板梯队（排序后第一层）之后，其余层级顺次下移
        return levelIndex === 0 ? levelRow + overlayRows : levelRow;
      }).join('')
      : '<div class="lt-cycle-none">无连板</div>' + overlayRows;
    // 成交额：只追加进当日列 tooltip（与既有日期/家数同一张卡片，原有 dayTip 内容一个字不动）
    const amountYi = cycleNum(seriesRow && seriesRow.amount);
    const amountTip = amountYi == null ? '' : `；成交额 ${amountYi.toFixed(1)} 亿`;
    // 选中态：只有真的选了这一天才会多出这个 class（默认态一个字节不变），纯视觉提示，不影响列宽
    const picked = cycleDayDate != null && cycleDayKey(cycleDayDate) === cycleDayKey(date)
      ? ' lt-cycle-col-pick'
      : '';
    return `<div class="lt-cycle-col lt-cycle-col-click${picked}" data-date="${esc(date)}" data-tip="${esc(dayTip + amountTip)}"><div class="lt-cycle-col-h${hot}${boardToneClass(maxHeight)}">${maxHeight == null ? '—' : maxHeight}</div>`
      + `<div class="lt-cycle-col-d">${esc(cycleShortDate(date))}</div>${rows}</div>`;
  }

  // 竞价成交额排版（入参单位：亿元）。按量级换单位、位数依次递减：
  //   ≥1亿      → 「亿」，保留两位小数（整数部分一位以上，如 3.32亿 / 12.60亿）
  //   百万、千万 → 「万」取整，天然落成 3 位（325万）或 4 位（3254万）
  //   十万及以下 → 「万」保留一位小数（52.3万 / 5.3万 / 0.5万）
  // null / 缺失 / 非有限数字一律返回 ''（调用方据此整行不渲染，不留空行）
  function formatAuctionAmount(value) {
    if (value == null || value === '') return '';
    const yi = Number(value);
    if (!Number.isFinite(yi) || yi < 0) return '';
    if (yi === 0) return '0';
    const wan = yi * 1e4;
    // 四舍五入后够 1 亿（如 0.99996 亿）就跳到亿单位，避免出现「10000万」
    if (yi >= 1 || Math.round(wan) >= 10000) return yi.toFixed(2) + '亿';
    if (wan >= 100) return Math.round(wan) + '万';
    return wan.toFixed(1) + '万';
  }

  // 竞价量比排版：保留 1~2 位小数（1.8 / 0.52 / 12.3），null / 非有限数字 → ''
  // 口径见后端：量比 = 竞价成交量(手)×100 ÷ 昨日成交量(股)
  function formatVolumeRatio(value) {
    if (value == null || value === '') return '';
    const ratio = Number(value);
    if (!Number.isFinite(ratio) || ratio < 0) return '';
    const rounded = Math.round(ratio * 100) / 100;
    // ≥10 只留 1 位（避免长串），其余最多 2 位且去掉末尾 0 → 1.8 而不是 1.80
    return rounded >= 10 ? rounded.toFixed(1) : String(rounded);
  }

  // 把「竞价成交额 / 竞价量比」写进表格里的竞额格：
  //   有量比 → 「0.52亿 / 1.8」；量比缺失 → 「0.52亿 / —」；金额缺失 → 整格留空（原行为）
  // 量比单独套 <span class="lt-auction-rat lt-ratio-hot|warm|cold">，颜色只靠 class 切换。
  // 分档：<0.5 浅灰；0.5 ≤ x < 2 浅红；≥2 亮红加粗。缺数据（—）保持默认中性色，不参与分档。
  function fillAuctionCell(el, amountText, ratioValue) {
    el.textContent = '';
    if (!amountText) { el.removeAttribute('title'); return; }
    const ratioText = formatVolumeRatio(ratioValue);
    const ratio = ratioValue == null || ratioValue === '' ? Number.NaN : Number(ratioValue);
    el.appendChild(document.createTextNode(amountText + ' / '));
    const tag = document.createElement('span');
    tag.className = 'lt-auction-rat';
    if (Number.isFinite(ratio) && ratio >= 2) tag.classList.add('lt-ratio-hot');
    else if (Number.isFinite(ratio) && ratio >= 0.5) tag.classList.add('lt-ratio-warm');
    else if (Number.isFinite(ratio)) tag.classList.add('lt-ratio-cold');
    tag.textContent = ratioText || '—';
    el.appendChild(tag);
    el.setAttribute('title', '竞价量比 = 竞价成交量 ÷ 昨日成交量（当日 09:25 竞价量 / 上一交易日全天成交量）');
  }

  // 把「竞价成交额」用 DOM 插进已有梯队方块：现成的名字 span 不动（只多带一个
  // data-auction-yi），外面套一层 .lt-cycle-stk 承接「名字 / 金额」两行，
  // 再 append 一个金额 span。不用 innerHTML 拼接，避免动到既有梯队布局；
  // 重渲染（60 秒轮询）时先把旧的 .lt-cycle-stk-amt 清掉再插，保证幂等、不会重复叠加。
  function decorateLadderAuction(root) {
    if (!root) return;
    const stocks = Array.from(root.querySelectorAll('.lt-cycle-ladder .lt-cycle-stock[data-auction-yi]'));
    stocks.forEach((stock) => {
      const text = formatAuctionAmount(stock.getAttribute('data-auction-yi'));
      if (!text) return;
      let holder = stock.parentNode;
      if (!holder || !holder.classList || !holder.classList.contains('lt-cycle-stk')) {
        holder = document.createElement('span');
        holder.className = 'lt-cycle-stk';
        stock.parentNode.insertBefore(holder, stock);
        holder.appendChild(stock);
      }
      Array.from(holder.getElementsByClassName('lt-cycle-stk-amt')).forEach((node) => node.remove());
      const line = document.createElement('span');
      line.className = 'lt-auction-amt lt-cycle-stk-amt';
      line.textContent = text;
      holder.appendChild(line);
    });
  }

  // 重绘会整体换掉 .lt-cycle-scroll（点某日梯队 / 切区间 / 轮询刷新都会），换之前先快照用户的横向位置。
  // 面板收起时元素没有布局（scrollWidth=0），这时不读，免得把 0 误记成「用户位置」把人家甩回最左
  function snapshotCycleScroll() {
    if (!cycleBox) return;
    const scroller = cycleBox.querySelector('.lt-cycle-scroll');
    if (!scroller || !scroller.isConnected || scroller.scrollWidth <= 0) return;
    cycleScrollLeft = scroller.scrollLeft;
  }

  // 用户「主动跳到某一天」（选日期弹层点日格 / 上一日 / 下一日）时，把天梯里那一天那一列滚进可视区。
  // 为什么这里的滚动是有意的：跳到远处的某天后天梯仍停在原处，被选中的那一列可能落在容器外上万像素，
  // 用户既看不到被选中的列，也判断不出自己跳到了哪一天 —— 这条路径必须跟随。
  // 为什么同时改 DOM 和记忆：renderCycle 的重绘会把 DOM 拉回 cycleScrollLeft 记的位置，
  // 只设 scrollLeft 不同步记忆，下一次重绘就「回弹」，等于没修。
  // 为什么点天梯里可见的列不滚：那条路径（onCycleDayPick）根本不调这里。用户点的是眼前已经看得见的
  // 东西，任何像素位移都是打扰 —— 上一轮「点一下甩到最右」的 bug 就是这种打扰，必须继续不发生。
  function scrollCycleToDay(date) {
    const key = cycleDayKey(date);
    if (!key || !cycleBox) return;
    const scroller = cycleBox.querySelector('.lt-cycle-scroll');
    if (!scroller || !scroller.isConnected || scroller.scrollWidth <= 0) return;
    // 目标列可能不在当前铺开的窗口里（选了别的年份 / 那天没有梯队）→ 什么都不做，绝不乱滚
    const col = Array.from(scroller.querySelectorAll('.lt-cycle-ladder .lt-cycle-col'))
      .find((node) => cycleDayKey(node.getAttribute('data-date')) === key);
    if (!col) return;
    const sr = scroller.getBoundingClientRect();
    const cr = col.getBoundingClientRect();
    if (!sr.width || !cr.width) return; // 面板收起时没有布局，等可见了再说
    // 整列本来就在可视区内（左右各留 1px 容差）→ 一个像素都不动，用户位置原样保留
    if (cr.left >= sr.left - 1 && cr.right <= sr.right + 1) return;
    // 居中对齐（列比容器还宽时居中也能保证列左缘可见），再夹紧到合法区间
    const centered = cr.left - sr.left + scroller.scrollLeft - (scroller.clientWidth - cr.width) / 2;
    const max = Math.max(0, scroller.scrollWidth - scroller.clientWidth);
    scroller.scrollLeft = Math.max(0, Math.min(centered, max));
    cycleScrollLeft = scroller.scrollLeft; // 关键：记忆同步成新位置，后续重绘才不会把它弹回去
  }

  // 「主动跳日」三个入口渲染完之后调用一次：拿当前报告日让天梯跟过去（列已可见则不动）
  function followCycleDay() {
    const item = history[current];
    scrollCycleToDay(item && item.date);
  }

  function renderCycle() {
    if (!cycleBox) return;
    try {
      cycleOverlayProbe = { cols: [] };
      const data = cycleData && typeof cycleData === 'object' ? cycleData : null;
      const dateText = data && data.date ? cycleShortDate(data.date) : '数据待接入';
      const parts = [`<div class="lt-cycle-title"><span>情绪周期</span><span class="lt-cycle-date">${esc(dateText)}</span></div>`];
      const builders = [
        () => renderCycleIndices(data && data.indices, data && data.indices_date),
        () => renderCycleAmount(data && data.amount),
        () => renderCycleTemperature(data && data.temperature, data && data.breadth),
        () => renderCycleWave(data),
      ];
      builders.forEach((build) => {
        try {
          parts.push(build());
        } catch {
          parts.push(cycleEmpty('数据待接入'));
        }
      });
      // 注意：这里不再快照——renderCycle 是被 mountCycle 「把 cycleBox 重新插回 body」之后调用的，
      // 那次插入本身就会把容器 scrollLeft 静默清零，此时读到的是 0 而不是用户位置。
      // 快照改在 mountCycle 插入之前做（见下方 mountCycle），要点日路径另有 paint / renderCycleDayView 的快照
      cycleBox.innerHTML = parts.join('');
      // 右上角日期控件里的按年翻页器跟着本次窗口刷新（标签年份 / 禁用态与图表里实际铺开的那一年一致）
      syncCycleYearPager();
      // 重渲染后旧 SVG（连同里面的悬停竖线）已被替换：顺手收起浮层并清掉失效引用
      hideCycleHover();
      // 梯队方块里补「竞价成交额」小字（DOM 插入，幂等：重渲染不会重复叠加）
      // 情绪波浪里的连板梯队不显示竞价成交额（用户口径）：decorateLadderAuction 保留备用、这里不再调用。
      // 共用横向滚动条：首帧钉到最右（最新交易日出现在右侧，历史向左滚动保留）；
      // 之后的重绘（点某日梯队 / 轮询刷新）一律还原用户当前的位置，绝不自动跳到最右
      const scroller = cycleBox.querySelector('.lt-cycle-scroll');
      if (scroller) {
        // 面板收起时元素没有布局（scrollWidth=0），这时不谈像素位置，保留记忆等可见后再还原
        if (scroller.scrollWidth > 0) {
          scroller.scrollLeft = cycleScrollLeft == null ? scroller.scrollWidth : cycleScrollLeft;
          cycleScrollLeft = scroller.scrollLeft; // 浏览器会把越界值夹紧到合法范围，回写实际值
        }
      }
      // 只读自检钩子：?probe=grid 时把「x 轴标签中线 vs 梯队卡片中线」的差值写进 <html data-gridprobe>
      if (location.search.indexOf('probe=grid') >= 0) {
        const labels = Array.from(cycleBox.querySelectorAll('.lt-cycle-axis span'));
        const cards = Array.from(cycleBox.querySelectorAll('.lt-cycle-ladder .lt-cycle-col'));
        const centerOf = (node) => {
          const box = node.getBoundingClientRect();
          return Math.round((box.left + box.width / 2) * 10) / 10;
        };
        const diffs = labels.map((label, index) => (cards[index]
          ? Math.round((centerOf(label) - centerOf(cards[index])) * 10) / 10
          : null));
        const maxAbs = diffs.reduce((max, value) => (value == null ? max : Math.max(max, Math.abs(value))), 0);
        document.documentElement.dataset.gridprobe = JSON.stringify({
          vw: window.innerWidth,
          cols: labels.length,
          cards: cards.length,
          first: labels.slice(0, 2).map((node) => node.textContent),
          last: labels.slice(-2).map((node) => node.textContent),
          maxAbs,
          diffs,
        });
      }
      // 只读自检钩子：?probe=overlay 时把当前窗口每一列的断板反包叠加层写进 <html data-overlayprobe>，
      // 结构 {cols:[{date, overlays:[{code,name,N,k,label}]}]}；与 ?probe=grid / ?probe=cols 互不影响
      if (location.search.indexOf('probe=overlay') >= 0) {
        document.documentElement.dataset.overlayprobe = JSON.stringify({
          cols: cycleOverlayProbe && Array.isArray(cycleOverlayProbe.cols) ? cycleOverlayProbe.cols : [],
        });
      }
    } catch {
      // 渲染异常只吞在区块内：下面的候选表格照常显示
      cycleBox.innerHTML = cycleEmpty('情绪周期数据待接入');
    }
  }

  function mountCycle() {
    if (!body) return;
    if (!cycleBox) {
      cycleBox = document.createElement('div');
      cycleBox.className = 'lt-cycle';
      // 事件委托：图例勾选框（renderCurrent 重建 body 时 cycleBox 本身不换，监听只挂一次）
      cycleBox.addEventListener('change', onCycleLegendChange);
      // 事件委托：点击当日列（梯队卡片=同一列网格）显示当日家数浮层；renderCurrent 重建卡片时不重绑
      cycleBox.addEventListener('click', onCycleDayClick);
      // 事件委托：点某一天 → 下方候选区切看那天的历史梯队（与上面那条并列，互不吞事件）
      cycleBox.addEventListener('click', onCycleDayPick);
      // 事件委托：区间三档切换（近两年 / 今年 / 去年）
      cycleBox.addEventListener('click', onCycleRangeClick);
      // 注意：按年翻页器不在 cycleBox 里，它的委托挂在 head 的日期控件容器上（见 .lt-head-actions 处）
      // 悬停预览：竖点划线 + 当日家数浮层（只预览，不改变点击选中态）
      cycleBox.addEventListener('mousemove', onCycleWaveHover);
      // 鼠标离开整个区块（含离开 SVG 后落到梯队卡片上）立即收起竖线与浮层
      cycleBox.addEventListener('mouseleave', hideCycleHover);
      // 拖动左轴量程：按下记起点，window 上的 mousemove/mouseup 在拖动期间才挂（见 onCycleDragStart）
      cycleBox.addEventListener('mousedown', onCycleDragStart);
      // 双击绘图区 = 回自动量程（override 置 null 后重绘）
      cycleBox.addEventListener('dblclick', onCycleWaveDblClick);
    }
    // 关键：把 cycleBox 重新插回 body 顶部这次「挪动」本身就会把容器 scrollLeft 静默清零
    // （不经过 JS setter，所以只能靠挪之前先快照）。用户点某日梯队后横向位置固定不动就靠这一步
    snapshotCycleScroll();
    if (cycleBox.parentNode !== body) body.insertBefore(cycleBox, body.firstChild);
    renderCycle();
  }

  async function loadCycle() {
    if (cycleLoading) return;
    cycleLoading = true;
    try {
      const response = await fetch(cycleUrl, { credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const text = await response.text();
      const parsed = JSON.parse(text);
      cycleData = parsed && typeof parsed === 'object' ? parsed : null;
    } catch {
      // /cycle.json 404、返回 HTML 或字段缺失：整块降级成占位，候选表格不受影响
      cycleData = null;
    } finally {
      cycleLoading = false;
    }
    try { renderCycle(); } catch { /* 保持主面板可读 */ }
    // 冷启动竞态兜底：/cycle.json 迟到时天梯先是占位（无列可滚），数据到位后按当前报告日补一次跟随。
    // 列已可见时 scrollCycleToDay 自己会什么都不做；history 为空时 item 为 undefined，同样静默返回。
    followCycleDay();
  }

  // 「竞价成交额」：09:25 集合竞价成交额（接口给亿元，显示按量级换亿/万），数据源 /auction.json。
  // 服务端现取官方撮合并做进程内缓存；这里只负责把值填进占位格，
  // 接口缺失 / 断网 / 非 JSON / 没有该代码 → 保持空白，绝不显示 null 或 NaN。
  // 当前该显示哪一天的竞价：历史日视图看被点那天，其余看当前报告日。
  // 日期只从数据字段（cycleDayDate / history[current].date）取，绝不 new Date() 现造。
  function currentAuctionDate() {
    if (cycleDayDate != null) return cycleDayKey(cycleDayDate);
    const item = history[current];
    return item ? cycleDayKey(item.date) : '';
  }

  async function fillAuctionAmounts(dateValue) {
    const holders = Array.from(body.querySelectorAll('[data-auction-code]'));
    if (!holders.length) return;
    const codes = Array.from(new Set(
      holders.map((el) => String(el.getAttribute('data-auction-code') || '').trim()).filter(Boolean),
    ));
    if (!codes.length) return;
    // 显式带上那天：不带 date 时服务端只给「今天」，历史视图就会把最近一场实时值串进来
    const day = cycleDayKey(dateValue) || currentAuctionDate();
    let items = null;
    try {
      const url = '/auction.json?codes=' + encodeURIComponent(codes.join(','))
        + (day ? '&date=' + day : '') + '&t=' + Date.now();
      const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
      if (response.ok) {
        const payload = await response.json();
        if (payload && typeof payload === 'object') items = payload.items || null;
      }
    } catch (error) {
      items = null;
    }
    if (!items) return;
    holders.forEach((el) => {
      const code = String(el.getAttribute('data-auction-code') || '').trim();
      const item = items[code] || null;
      const yi = item ? Number(item.amount_yi) : Number.NaN;
      // 量比缺数据（回填未完成 / 新股无昨日成交量）时显示「—」，绝不显示 null/NaN
      fillAuctionCell(
        el,
        Number.isFinite(yi) ? formatAuctionAmount(yi) : '',
        item ? item.volume_ratio : null,
      );
    });
  }

  // 选中情绪波浪里的某一天时，只换下方候选区的内容：面板顶上的日期 / 副标题始终是「最新那份报告」的语义，
  // 状态条写在候选区自己身上（不塞进图表、不塞进日期键行），并带一个「回到最新」的出口。
  // 情绪波浪区块照旧钉回 body 最前面，用户才能继续点别的日子或点「回到最新」
  function renderCycleDayView() {
    // 点某日会整体换掉 body（连同里面的 .lt-cycle-scroll）→ 换之前先记住用户看到的横向位置，
    // 这样点完梯队后横向滚动条还停在原地，不会自己顶到最右侧
    snapshotCycleScroll();
    const data = cycleLastData && typeof cycleLastData === 'object' ? cycleLastData : cycleData;
    const key = cycleDayKey(cycleDayDate);
    // 本次渲染要求的序号：缓存命中就同步出日报；没命中先出梯队快照占位，等日报回来再重绘。
    // 期间用户若点了别的日子 / 点了「回到最新」，seq 已经变了，过期的响应直接丢弃，绝不覆盖当前画面
    const seq = ++cycleDayReportSeq;
    const paint = (state) => {
      if (seq !== cycleDayReportSeq) return;
      if (cycleDayDate == null) return;
      // 每次 paint 都会整体换掉 body（日报回来还会再换一次）→ 换之前都先记住横向位置
      snapshotCycleScroll();
      if (state && state.text) {
        const html = cycleDayReportMarkup(state.text);
        body.innerHTML = cycleDayReportBarMarkup(key)
          + (html.trim() ? html : '<div class="lt-empty"><b>该日日报没有候选数据</b></div>');
        // 日报里的阶段表与最新态是同一张表：提示浮层、排序箭头、跨表列宽同步都要绑上
        bindTooltips();
        syncTableColumns();
      } else {
        // 没有日报 / 日报还在路上：先显示原有的梯队快照（那天有什么连板股仍然看得到）；
        // 确认无日报后再把状态条换成「该日无日报……下面是该日梯队快照」的准确说法
        body.innerHTML = cycleDaySnapshotMarkup(data, cycleDayDate)
          || '<div class="lt-empty"><b>该日无梯队数据</b></div>';
        if (state && state.missing) {
          const oldBar = body.querySelector('.lt-day-bar');
          if (oldBar) oldBar.remove();
          body.insertAdjacentHTML('afterbegin', cycleDayMissingBarMarkup(key));
        }
      }
      mountCycle();
      // 历史日视图原先从不补竞价子行 → 点某天进来后「竞价量比 / 竞价成交额」必空白；
      // 这里按被点那天补一次（body 已被整体换掉，holders 取的就是新 DOM）
      fillAuctionAmounts(key);
      // 顶部「交易日」与底栏页码也必须钉到被点那天：候选表已经换成那天的日报了，
      // 这两处若还写最新报告日（= 当前年份最后一日），用户看到的就是「点某天却跳到该年最后一日」
      dateEl.textContent = '交易日 ' + displayDate(key);
      const shownIndex = Array.isArray(history)
        ? history.findIndex((row) => row && cycleDayKey(row.date) === key)
        : -1;
      posEl.innerHTML = shownIndex >= 0
        ? `<b>${shownIndex + 1}</b> / ${history.length}`
        : '<b>—</b>';
      syncDatePop(); // 日期面板若开着：高亮与「当前日」一起跟到被点那天
      statusEl.textContent = '情绪波浪选中的历史日 · 非最新';
      body.scrollTop = 0;
    };
    const cached = key ? cycleDayReportCache.get(key) : null;
    paint(cached || null);
    // 日报取回后再重绘一次；取不到（404）也要走一遍 paint，把状态条换成「该日无日报」的说法
    if (key && !cached) fetchCycleDayReport(key).then(paint).catch(() => {});
  }

  function renderCurrent() {
    // 情绪波浪选中了某一天 → 下方候选区换成那天的历史快照；
    // cycleDayDate 默认 null，老路径一个字节都不变
    if (cycleDayDate != null) { renderCycleDayView(); return; }
    if (!history.length) { showEmpty(); return; }
    // 换 body 之前先记住横向滚动位置：轮询 / 翻页重绘同样不许把梯队列顶到最右
    snapshotCycleScroll();
    current = Math.max(0, Math.min(current, history.length - 1));
    const item = history[current];
    lastMarkdown = item.markdown;
    const extracted = extractEmotionDetails(item.markdown);
    const extractedEvent = extractEventDetails(extracted.markdown);
    const html = renderMarkdown(extractedEvent.markdown, extracted.details, extractedEvent.details);
    body.innerHTML = html.trim() ? html : '<div class="lt-empty"><b>这份报告没有候选数据</b></div>';
    bindTooltips();
    mountCycle(); // 情绪周期区块钉在内容最上方；/cycle.json 缺失时只显示占位，不影响下面的表格
    fillAuctionAmounts(item.date); // 竞价成交额（亿元）按该报告日异步补进「竞额20日比%」格；接口缺失时格子留空
    syncTableColumns(); // 表格渲染完立刻统一各节列宽，避免 3→4 / 4→5 跨表列起点错开
    // 回归自检钩子：?probe=cols 时把每张表的列左边界与溢出情况写进 <html data-colprobe>，
    // 用 headless --dump-dom 就能核对「三张表逐列对齐且没有串字」。
    if (location.search.indexOf('probe=cols') >= 0) {
      const probe = Array.from(body.querySelectorAll('table.lt-table')).map((table) => {
        const ths = Array.from(table.querySelectorAll('thead th'));
        let overflow = 0;
        table.querySelectorAll('th,td').forEach((cell) => {
          if (cell.scrollWidth > cell.clientWidth + 1) overflow += 1;
        });
        return {
          cols: ths.length,
          rows: table.tBodies[0] ? table.tBodies[0].rows.length : 0,
          width: Math.round(table.getBoundingClientRect().width),
          overflow,
          lefts: ths.map((th) => Math.round(th.getBoundingClientRect().left * 10) / 10),
        };
      });
      document.documentElement.dataset.colprobe = JSON.stringify(probe);
    }
    dateEl.textContent = item.date ? `交易日 ${displayDate(item.date)}` : '—';
    const count = countCandidates(item.markdown);
    statusEl.textContent = `历史 ${current + 1}/${history.length} · ${count} 只候选`;
    posEl.innerHTML = `<b>${current + 1}</b> / ${history.length}`;
    prev.disabled = current <= 0;
    next.disabled = current >= history.length - 1;
    latest.disabled = current >= history.length - 1;
    syncDatePop(); // 面板开着时同步高亮，避免 prev/next 或轮询后高亮停在旧日期
    body.scrollTop = 0;
  }

  async function load() {
    if (busy) return;
    hideTip();
    closeDatePop(); // 刷新会重建 body 与 history，弹层先收起，避免停在旧日期上
    loadCycle(); // 顺带刷新情绪周期数据；该请求失败只影响区块本身
    busy = true;
    refresh.disabled = true;
    prev.disabled = true;
    next.disabled = true;
    latest.disabled = true;
    body.innerHTML = '<div class="lt-loading"><div class="lt-skeleton"></div><div class="lt-skeleton"></div><div class="lt-skeleton"></div><p>读取最近报告…</p></div>';
    statusEl.textContent = '读取中…';
    try {
      const response = await fetch(latestUrl, { credentials: 'same-origin' });
      const markdown = await response.text();
      if (!response.ok) throw new Error(markdown || ('HTTP ' + response.status));
      lastPayload = markdown;
      history = parseHistory(markdown);
      current = history.length - 1;
      renderCurrent();
    } catch (error) {
      body.innerHTML = `<div class="lt-error">读取失败：${esc(error && error.message ? error.message : error)}</div>`;
      mountCycle(); // 报告读取失败时区块仍在，只显示占位
      statusEl.textContent = '读取失败';
    } finally {
      busy = false;
      // 数据刷新轮询还在跑时保持禁用，等它的 finally 统一恢复，免得按钮中途变可点
      refresh.disabled = Boolean(dataRefreshTask);
    }
  }

  // 版本戳：latest.md 已涨到 50MB，每分钟整份重下是内存爬升的根因。
  // 先取几百字节的 /latest.stamp（mtime+size），值没变就跳过全量拉取；
  // 再留 30 分钟强制重读一次兜底，防止极端情况下 mtime/size 相同但需要重读。
  const FULL_RELOAD_MS = 30 * 60 * 1000;
  let lastStamp = '';
  let lastStampAt = 0;

  async function pollLatest() {
    if (busy || !panel.classList.contains('open') || document.hidden) return;
    if (current < history.length - 1) return;
    try {
      const stampUrl = latestUrl.endsWith('/latest.md')
        ? latestUrl.slice(0, -'latest.md'.length) + 'latest.stamp'
        : '';
      if (stampUrl) {
        let stamp = '';
        try {
          const stampRes = await fetch(stampUrl, { credentials: 'same-origin', cache: 'no-store' });
          if (stampRes.ok) stamp = (await stampRes.text()).trim();
        } catch (_) {
          stamp = ''; // 取不到版本戳就走全量，行为与旧版一致
        }
        if (stamp && stamp === lastStamp && Date.now() - lastStampAt < FULL_RELOAD_MS) return;
        if (stamp) {
          lastStamp = stamp;
          lastStampAt = Date.now();
        }
      }
      const response = await fetch(latestUrl, {
        credentials: 'same-origin',
        cache: 'no-store',
      });
      if (!response.ok) return;
      const markdown = await response.text();
      if (markdown === lastPayload) return;
      lastPayload = markdown;
      history = parseHistory(markdown);
      current = history.length - 1;
      renderCurrent();
    } catch {
      /* keep the current panel on a transient poll failure */
    }
  }

  // ===== 数据刷新：GET /refresh 触发一次全市场重建，再轮询 /refresh.json 等到 running=false =====
  // 字段契约（后端 tools/panel_server.py）：running/phase/started_at/finished_at/ok/rc/log/
  // data_date/expected_date/stale/trading_day/auto_times/catchup_times。前端只读不算，字段缺失一律兜底。
  // 请求前缀：独立窗口（8765）走 __DSH_LONGTO_SERVER，内嵌 DSH 页面走同源相对路径。
  const dataApiBase = (typeof window.__DSH_LONGTO_SERVER === 'string') ? window.__DSH_LONGTO_SERVER : '';
  const REFRESH_POLL_MS = 2000; // 轮询间隔：每 2 秒问一次状态
  const REFRESH_TIMEOUT_MS = 10 * 60 * 1000; // 10 分钟硬超时：按钮绝不能永久停在进行中态
  const REFRESH_FAIL_LIMIT = 5; // 连续这么多次拿不到状态（≈10s）就收手，退回「只重载报告」
  const AUTO_CATCHUP_KEY = 'lt-auto-catchup'; // 会话级标记：同一浏览器会话最多自动补刷一次
  const autoEl = panel.querySelector('#dsh-longtou-auto');

  // 自动执行时间表：优先用后端 /refresh.json 的 auto_times / catchup_times，取不到才用硬编码兜底
  const AUTO_TIMES_FALLBACK = ['09:25', '11:30', '13:00', '15:00'];
  const CATCHUP_TIMES_FALLBACK = ['15:30', '16:30'];
  const AUTO_TIME_HINTS = {
    '09:25': '竞价结束：抓集合竞价，出 09:25 信号快照',
    '11:30': '上午收盘：跑一次半日数据，盘中候选先更新',
    '13:00': '下午开盘：补齐午间缺口，保持盘中数据连续',
    '15:00': '收盘全量重建：全市场日线 / 竞价 / 流通股本一次刷完',
    '15:30': '兜底补跑：15:00 没跑成时补一次',
    '16:30': '兜底补跑：当天最后一次机会，错过就等下一个交易日',
  };

  // 只留非空字符串时刻；整份数组不可用时退回硬编码兜底（后端没上线也不至于显示空白）
  function cleanTimes(list, fallback) {
    const times = Array.isArray(list)
      ? list.filter((item) => typeof item === 'string' && item.trim()).map((item) => item.trim())
      : [];
    return times.length ? times : fallback.slice();
  }

  function describeAutoTime(time, kind) {
    const text = String(time || '');
    if (AUTO_TIME_HINTS[text]) return `${text} ${AUTO_TIME_HINTS[text]}`;
    return kind === 'catchup' ? `${text} 兜底补跑一次数据刷新` : `${text} 自动执行一次数据刷新`;
  }

  // 文案和 hover 提示都由同一份数组生成：时间表只维护一处（后端 /refresh.json 优先）
  function renderAutoTimes(info) {
    if (!autoEl) return;
    const auto = cleanTimes(info && info.auto_times, AUTO_TIMES_FALLBACK);
    const catchup = cleanTimes(info && info.catchup_times, CATCHUP_TIMES_FALLBACK);
    autoEl.textContent = `自动刷新 ${auto.join(' · ')}（周一至周五）｜兜底 ${catchup.join(' · ')}`;
    autoEl.title = ['自动刷新（周一至周五）']
      .concat(auto.map((time) => '· ' + describeAutoTime(time, 'auto')))
      .concat(['兜底补跑（自动刷新失败时补一次）'])
      .concat(catchup.map((time) => '· ' + describeAutoTime(time, 'catchup')))
      .join('\n');
  }

  function sleep(ms) {
    return new Promise((resolve) => { setTimeout(resolve, ms); });
  }

  async function readRefreshJson() {
    const response = await fetch(dataApiBase + '/refresh.json?t=' + Date.now(), { cache: 'no-store' });
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const text = await response.text();
    // 后端还没上线时这里可能是 404 的 HTML：JSON.parse 抛错，由调用方兜底
    return JSON.parse(text);
  }

  // 角落提示条：右下角一行小字，6 秒后淡出；不挡点击，也绝不抛异常
  let toastEl = null;
  let toastTimer = null;
  function showToast(text) {
    try {
      if (!toastEl) {
        toastEl = document.createElement('div');
        toastEl.id = 'dsh-longtou-toast';
        (document.body || document.documentElement).appendChild(toastEl);
      }
      toastEl.textContent = text;
      clearTimeout(toastTimer);
      toastEl.classList.add('lt-in');
      toastTimer = setTimeout(() => { toastEl.classList.remove('lt-in'); }, 6000);
    } catch (error) {
      console.warn('[longtou] 提示条渲染失败', error);
    }
  }

  function setRefreshBusy(on) {
    if (!refresh) return;
    refresh.disabled = on;
    refresh.textContent = on ? '刷新数据中…' : '刷新';
    refresh.title = on ? '正在后台刷新数据，完成后自动重载报告' : '刷新报告，并触发一次数据刷新';
  }

  // 刷新键：先触发数据刷新，轮询结束后才重载报告；已在跑时复用同一个任务，不重复触发
  function startDataRefresh() {
    if (dataRefreshTask) return dataRefreshTask;
    hideTip();
    setRefreshBusy(true);
    statusEl.textContent = '刷新数据中…';
    dataRefreshTask = runDataRefresh().finally(() => {
      dataRefreshTask = null;
      setRefreshBusy(false); // 无论成功/失败/超时，按钮一定回到可用态
    });
    return dataRefreshTask;
  }

  async function runDataRefresh() {
    let info = null;
    try {
      // 1) 触发：后端兼容 GET / POST，这里用 GET；接口非阻塞，立即返回
      const response = await fetch(dataApiBase + '/refresh?t=' + Date.now(), { cache: 'no-store' });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      // 2) 轮询：running 变 false 才结束；连续读不到状态或超过 10 分钟都按结束处理
      const deadline = Date.now() + REFRESH_TIMEOUT_MS;
      let misses = 0;
      while (Date.now() < deadline) {
        await sleep(REFRESH_POLL_MS); // 先等一拍，给刚触发的刷新写入 running=true 的机会
        try {
          info = await readRefreshJson();
        } catch (error) {
          if (++misses >= REFRESH_FAIL_LIMIT) throw error;
          continue; // 单次抖动不算失败
        }
        misses = 0;
        if (info && info.running === false) break;
        statusEl.textContent = '刷新数据中…' + (info && info.phase ? ' ' + String(info.phase) : '');
      }
    } catch (error) {
      // 接口没上线 / 网络异常 / 服务正在重启：静默降级成原来的「只重载报告」，面板照常可用
      console.warn('[longtou] 数据刷新失败，仅重载报告', error);
      await load();
      statusEl.textContent = '数据刷新失败（' + (error && error.message ? error.message : error) + '）';
      return;
    }
    renderAutoTimes(info); // 顺带同步后端最新的时间表
    await load(); // 3) 原刷新键的行为：重拉 /latest.md 与 /cycle.json
    if (info && info.running === true) {
      statusEl.textContent = '刷新超时：10 分钟仍未结束，后台可能还在跑';
      showToast('数据刷新超时：后台可能仍在重建，稍后手动再刷新一次');
      return;
    }
    if (info && info.ok === true) {
      showToast('数据已更新' + (info.data_date ? ' · ' + displayDate(info.data_date) : ''));
      return;
    }
    if (info && info.ok === false) {
      // 失败要能一眼定位：退出码 + 日志路径都摆出来
      const rc = (info.rc === null || info.rc === undefined) ? '?' : info.rc;
      const logPath = info.log || 'output/refresh_manual.log';
      statusEl.textContent = `数据刷新失败 · rc=${rc} · ${logPath}`;
      showToast(`数据刷新失败 · rc=${rc} · 日志 ${logPath}`);
      return;
    }
    statusEl.textContent = '报告已重载（未拿到刷新状态）';
  }

  // 进入面板时的自动补刷：数据落后 + 没在跑 + 本会话没补过，才触发一次；任何异常都静默
  async function autoCatchup() {
    let info = null;
    try {
      info = await readRefreshJson();
    } catch (error) {
      console.warn('[longtou] /refresh.json 不可用，跳过自动补刷', error);
      return; // 后端没上线：什么都不做，面板照常显示
    }
    renderAutoTimes(info); // 顺手用真实时间表覆盖兜底文案
    if (!info || info.stale !== true || info.running !== false) return;
    try {
      if (sessionStorage.getItem(AUTO_CATCHUP_KEY) === '1') return; // 同一会话只补一次，避免反复全市场重建
      sessionStorage.setItem(AUTO_CATCHUP_KEY, '1');
    } catch (error) {
      // 隐私模式禁用 sessionStorage：拿不到标记就宁可不动
      console.warn('[longtou] sessionStorage 不可用，跳过自动补刷', error);
      return;
    }
    showToast('检测到数据落后，已在后台补刷');
    await startDataRefresh(); // 与手动刷新共用同一套轮询与超时兜底
  }

  renderAutoTimes(null); // 先用兜底时刻渲染，等 /refresh.json 回来再覆盖

  function beginDrag(event) {
    if (event.button !== 0 || panel.classList.contains('lt-max')) return;
    if (event.target.closest('button, input, select, textarea, a')) return;
    closeDatePop(); // 拖动开始就收起日期面板
    if (panel.dataset.snapped && preSnapLayout) {
      const current = panel.getBoundingClientRect();
      panel.style.width = `${preSnapLayout.width}px`;
      panel.style.height = `${preSnapLayout.height}px`;
      panel.style.left = `${Math.max(8, Math.min(current.left, window.innerWidth - preSnapLayout.width - 8))}px`;
      panel.style.top = `${Math.max(8, Math.min(current.top, window.innerHeight - preSnapLayout.height - 8))}px`;
      delete panel.dataset.snapped;
      preSnapLayout = null;
      updateNarrow();
    }
    const rect = panel.getBoundingClientRect();
    dragState = {
      id: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
      moved: false,
    };
    panel.style.left = `${rect.left}px`;
    panel.style.top = `${rect.top}px`;
    panel.style.right = 'auto';
    panel.style.bottom = 'auto';
    panel.classList.add('lt-dragging');
    try { head.setPointerCapture(event.pointerId); } catch { /* older browsers */ }
    event.preventDefault();
  }

  function moveDrag(event) {
    if (!dragState || event.pointerId !== dragState.id) return;
    if (Math.abs(event.clientX - dragState.startX) > 3 || Math.abs(event.clientY - dragState.startY) > 3) {
      dragState.moved = true;
    }
    const position = clampPosition(
      event.clientX - dragState.offsetX,
      event.clientY - dragState.offsetY,
    );
    panel.style.left = `${position.left}px`;
    panel.style.top = `${position.top}px`;
  }

  function snapHalf(side) {
    if (panel.classList.contains('lt-max')) return;
    if (!panel.dataset.snapped) {
      preSnapLayout = { width: panel.offsetWidth, height: panel.offsetHeight };
    }
    panel.dataset.snapped = side;
    const gap = 8;
    const width = window.innerWidth < 760
      ? window.innerWidth - gap * 2
      : Math.max(360, Math.floor(window.innerWidth / 2) - gap);
    const height = window.innerHeight - gap * 2;
    panel.style.width = `${width}px`;
    panel.style.height = `${height}px`;
    panel.style.top = `${gap}px`;
    panel.style.left = side === 'right' ? `${window.innerWidth - width - gap}px` : `${gap}px`;
    panel.style.right = 'auto';
    panel.style.bottom = 'auto';
    panel.classList.add('lt-snapped');
    setTimeout(() => panel.classList.remove('lt-snapped'), 320);
    updateNarrow();
    saveLayout();
  }

  function endDrag() {
    if (!dragState) return;
    const moved = dragState.moved;
    dragState = null;
    panel.classList.remove('lt-dragging');
    saveLayout();
    if (!moved) return;
    const rect = panel.getBoundingClientRect();
    if (rect.left <= 10) snapHalf('left');
    else if (rect.right >= window.innerWidth - 10) snapHalf('right');
  }

  function beginResize(event) {
    if (event.button !== 0 || panel.classList.contains('lt-max')) return;
    event.preventDefault();
    event.stopPropagation();
    closeDatePop(); // 缩放开始同样收起日期面板（弹层是 fixed，会与面板脱节）
    delete panel.dataset.snapped;
    preSnapLayout = null;
    const rect = panel.getBoundingClientRect();
    resizeState = {
      id: event.pointerId,
      dir: event.currentTarget.dataset.dir || 'se',
      startX: event.clientX,
      startY: event.clientY,
      left: rect.left,
      top: rect.top,
      width: rect.width,
      height: rect.height,
    };
    panel.style.left = `${rect.left}px`;
    panel.style.top = `${rect.top}px`;
    panel.style.width = `${rect.width}px`;
    panel.style.height = `${rect.height}px`;
    panel.style.right = 'auto';
    panel.style.bottom = 'auto';
    panel.classList.add('lt-resizing');
    try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* older browsers */ }
  }

  function moveResize(event) {
    if (!resizeState || event.pointerId !== resizeState.id) return;
    const gap = 8;
    const minWidth = 360;
    const minHeight = 260;
    const dx = event.clientX - resizeState.startX;
    const dy = event.clientY - resizeState.startY;
    const dir = resizeState.dir;
    let left = resizeState.left;
    let top = resizeState.top;
    let width = resizeState.width;
    let height = resizeState.height;

    if (dir.includes('e')) {
      width = Math.max(minWidth, Math.min(resizeState.width + dx, window.innerWidth - resizeState.left - gap));
    }
    if (dir.includes('s')) {
      height = Math.max(minHeight, Math.min(resizeState.height + dy, window.innerHeight - resizeState.top - gap));
    }
    if (dir.includes('w')) {
      width = Math.max(minWidth, Math.min(resizeState.width - dx, resizeState.left + resizeState.width - gap));
      left = resizeState.left + resizeState.width - width;
    }
    if (dir.includes('n')) {
      height = Math.max(minHeight, Math.min(resizeState.height - dy, resizeState.top + resizeState.height - gap));
      top = resizeState.top + resizeState.height - height;
    }
    left = Math.max(gap, Math.min(left, window.innerWidth - width - gap));
    top = Math.max(gap, Math.min(top, window.innerHeight - height - gap));

    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
    panel.style.width = `${width}px`;
    panel.style.height = `${height}px`;
    updateNarrow();
  }

  function endResize() {
    if (!resizeState) return;
    resizeState = null;
    panel.classList.remove('lt-resizing');
    updateNarrow();
    saveLayout();
  }

  head.addEventListener('pointerdown', beginDrag);
  head.addEventListener('pointermove', moveDrag);
  head.addEventListener('pointerup', endDrag);
  head.addEventListener('pointercancel', endDrag);
  panel.querySelectorAll('.lt-resize').forEach((handle) => {
    handle.addEventListener('pointerdown', beginResize);
    handle.addEventListener('pointermove', moveResize);
    handle.addEventListener('pointerup', endResize);
    handle.addEventListener('pointercancel', endResize);
  });
  minBtn.addEventListener('click', () => setMinimized(!panel.classList.contains('lt-min')));
  maxBtn.addEventListener('click', () => setMaximized(!panel.classList.contains('lt-max')));
  applyTheme(savedTheme()); // 默认暗色；有 localStorage 记录就沿用上次
  themeBtn.addEventListener('click', () => {
    applyTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light');
  });
  window.addEventListener('resize', () => {
    if (panel.classList.contains('lt-max')) return;
    if (!panel.style.left) return;
    const width = Math.min(panel.offsetWidth, window.innerWidth - 16);
    const height = Math.min(panel.offsetHeight, window.innerHeight - 16);
    if (width !== panel.offsetWidth) panel.style.width = `${width}px`;
    if (height !== panel.offsetHeight) panel.style.height = `${height}px`;
    const position = clampPosition(parseInt(panel.style.left, 10), parseInt(panel.style.top, 10), width, height);
    panel.style.left = `${position.left}px`;
    panel.style.top = `${position.top}px`;
    saveLayout();
  });
  applyLayout();

  function clampFab(left, top) {
    const size = fab.offsetWidth || 48;
    return {
      left: Math.max(8, Math.min(Number(left) || 8, window.innerWidth - size - 8)),
      top: Math.max(8, Math.min(Number(top) || 8, window.innerHeight - size - 8)),
    };
  }

  function setFabPosition(left, top) {
    const position = clampFab(left, top);
    fab.style.left = `${position.left}px`;
    fab.style.top = `${position.top}px`;
    fab.style.right = 'auto';
    fab.style.bottom = 'auto';
  }

  function saveFabPosition() {
    try {
      localStorage.setItem(fabKey, JSON.stringify({
        left: parseInt(fab.style.left, 10),
        top: parseInt(fab.style.top, 10),
      }));
    } catch { /* ignore */ }
  }

  function restoreFabPosition() {
    try {
      const saved = JSON.parse(localStorage.getItem(fabKey) || 'null');
      if (saved && Number.isFinite(saved.left) && Number.isFinite(saved.top)) {
        setFabPosition(saved.left, saved.top);
      }
    } catch { /* ignore */ }
  }

  function beginFabDrag(event) {
    if (event.button !== 0) return;
    const rect = fab.getBoundingClientRect();
    fabDrag = {
      id: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
      moved: false,
    };
    fab.style.left = `${rect.left}px`;
    fab.style.top = `${rect.top}px`;
    fab.style.right = 'auto';
    fab.style.bottom = 'auto';
    fab.classList.add('lt-fab-dragging');
    try { fab.setPointerCapture(event.pointerId); } catch { /* older browsers */ }
  }

  function moveFabDrag(event) {
    if (!fabDrag || event.pointerId !== fabDrag.id) return;
    if (Math.abs(event.clientX - fabDrag.startX) > 3 || Math.abs(event.clientY - fabDrag.startY) > 3) {
      fabDrag.moved = true;
    }
    setFabPosition(event.clientX - fabDrag.offsetX, event.clientY - fabDrag.offsetY);
  }

  function endFabDrag() {
    if (!fabDrag) return;
    const moved = fabDrag.moved;
    fabDrag = null;
    fab.classList.remove('lt-fab-dragging');
    if (moved) {
      suppressFabClick = true;
      setTimeout(() => { suppressFabClick = false; }, 350);
      saveFabPosition();
    }
  }

  restoreFabPosition();
  fab.addEventListener('pointerdown', beginFabDrag);
  fab.addEventListener('pointermove', moveFabDrag);
  fab.addEventListener('pointerup', endFabDrag);
  fab.addEventListener('pointercancel', endFabDrag);
  window.addEventListener('resize', () => {
    if (fab.style.left) setFabPosition(parseInt(fab.style.left, 10), parseInt(fab.style.top, 10));
  });

  fab.addEventListener('click', (event) => {
    if (suppressFabClick) {
      event.preventDefault();
      event.stopPropagation();
      suppressFabClick = false;
      return;
    }
    panel.classList.toggle('open');
    if (panel.classList.contains('open')) {
      updateNarrow();
      if (!history.length) load();
      playRestartEnter(); // reload 后的「重新打开」入场动画（仅重启成功时命中标记）
    } else {
      closeDatePop(); // 面板收起时弹层不能留在页面上
    }
  });
  // 仅独立窗口支持：把浏览器窗口本身设为系统级置顶（等同微信聊天窗口的置顶）
  // 内嵌在 DSH 页面里的面板受浏览器限制无法做窗口级置顶，因此那里不显示该按钮
  const topmostUrl = window.__DSH_LONGTOU_TOPMOST ? '/topmost' : '';
  function syncTopmost(on) {
    if (!topmostUrl) return;
    try {
      fetch(`${topmostUrl}?on=${on ? 1 : 0}`, { cache: 'no-store' }).catch(() => {});
    } catch { /* ignore */ }
  }
  function applyTopmost(value) {
    const on = Boolean(value);
    panel.classList.toggle('lt-on-top', on);
    pinBtn.textContent = on ? '已置顶' : '置顶';
    pinBtn.title = on ? '取消窗口置顶' : '窗口置顶';
    try { localStorage.setItem(pinKey, on ? '1' : '0'); } catch { /* ignore */ }
    syncTopmost(on);
  }
  if (!topmostUrl) {
    pinBtn.style.display = 'none';
  } else {
    let pinned = false;
    try { pinned = localStorage.getItem(pinKey) === '1'; } catch { /* ignore */ }
    applyTopmost(pinned);
    pinBtn.addEventListener('click', (event) => {
      event.stopPropagation();
      applyTopmost(!panel.classList.contains('lt-on-top'));
    });
  }
  refresh.addEventListener('click', startDataRefresh); // 刷新键：先触发数据刷新，跑完再重载报告（原行为）
  // 上一日 / 下一日属于「用户主动跳日」：重绘完把天梯跟到那一天（那一列本来就可见则一个像素都不动）
  prev.addEventListener('click', () => { cycleDayDate = null; current -= 1; renderCurrent(); followCycleDay(); });
  next.addEventListener('click', () => { cycleDayDate = null; current += 1; renderCurrent(); followCycleDay(); });
  latest.addEventListener('click', () => { cycleDayDate = null; current = history.length - 1; renderCurrent(); followCycleDay(); });
  // 候选区状态条上的「回到最新」：内容随渲染重建，所以在 body 上做事件委托（与情绪波浪同一范式）
  body.addEventListener('click', (event) => {
    const back = event.target && event.target.closest ? event.target.closest('[data-cycle-day-back]') : null;
    if (!back || !body.contains(back)) return;
    event.preventDefault();
    event.stopPropagation();
    cycleDayDate = null;
    renderCurrent();
  });
  // 「选日期」按钮：点击展开/收起日期面板；stopPropagation 防止被 document 的外点委托立刻关掉
  pickBtn.addEventListener('click', (event) => {
    event.stopPropagation();
    if (datePop.classList.contains('open')) { closeDatePop(); return; }
    openDatePop();
  });
  // 点格子选日期（事件委托：格子每次打开都重建）
  datePop.addEventListener('click', (event) => {
    const cell = event.target.closest('.lt-day');
    if (!cell) return;
    event.stopPropagation();
    const index = Number(cell.getAttribute('data-date-index'));
    if (!Number.isInteger(index) || index < 0 || index >= history.length) return;
    cycleDayDate = null; // 从日期面板选报告日 = 回报告视图，别停在情绪波浪选中的那天
    current = index;
    renderCurrent();
    followCycleDay(); // 主动跳日：天梯跟着把那一天那一列滚进可视区（点可见列的老路径不走这里）
    closeDatePop();
  });
  copy.addEventListener('click', async () => {
    if (!lastMarkdown) return;
    try {
      await navigator.clipboard.writeText(lastMarkdown);
      statusEl.textContent = '已复制 Markdown';
    } catch {
      statusEl.textContent = '复制失败';
    }
  });
  fontRange.addEventListener('input', () => applyFontSize(fontRange.value));
  settingsBtn.addEventListener('click', (event) => {
    event.stopPropagation();
    const opening = !settingsPop.classList.contains('open');
    settingsPop.classList.toggle('open', opening);
    settingsBtn.setAttribute('aria-expanded', opening ? 'true' : 'false');
    if (!opening) return;
    const rect = settingsBtn.getBoundingClientRect();
    const width = settingsPop.offsetWidth;
    const height = settingsPop.offsetHeight;
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
    let top = rect.bottom + 8;
    if (top + height > window.innerHeight - 8) top = Math.max(8, rect.top - height - 8);
    settingsPop.style.left = `${left}px`;
    settingsPop.style.top = `${top}px`;
    settingsPop.style.bottom = 'auto';
  });
  alphaBtn.addEventListener('click', (event) => {
    event.stopPropagation();
    alphaPop.classList.toggle('open');
    settingsPop.classList.remove('open');
  });
  marketBtn.addEventListener('click', (event) => {
    event.stopPropagation();
    SHOW_MARKET_SECTION = !SHOW_MARKET_SECTION;
    marketBtn.classList.toggle('lt-on', SHOW_MARKET_SECTION);
    marketBtn.setAttribute('aria-pressed', SHOW_MARKET_SECTION ? 'true' : 'false');
    marketBtn.title = SHOW_MARKET_SECTION ? '市场指标（点击隐藏）' : '市场指标（点击显示）';
    renderCurrent();
  });
  // 「盘前四问 / 封单口径 / 打板纪律」折叠开关：只切面板上的 class，内容已渲染在 DOM 里，无需重渲染
  notesBtn.addEventListener('click', (event) => {
    event.stopPropagation();
    const on = panel.classList.toggle('lt-notes-on');
    notesBtn.classList.toggle('lt-on', on);
    notesBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    notesBtn.title = on ? '盘前四问与纪律（点击隐藏）' : '盘前四问与纪律（点击显示）';
  });
  document.addEventListener('click', (event) => {
    if (!settingsPop.contains(event.target) && !settingsBtn.contains(event.target)) {
      settingsPop.classList.remove('open');
      settingsBtn.setAttribute('aria-expanded', 'false');
    }
    if (!alphaPop.contains(event.target) && !alphaBtn.contains(event.target)) {
      alphaPop.classList.remove('open');
    }
    if (!datePop.contains(event.target) && !pickBtn.contains(event.target)) closeDatePop();
    if (!event.target.closest('.lt-emotion-btn') && !event.target.closest('.lt-event-btn')) hideTip();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      settingsPop.classList.remove('open');
      settingsBtn.setAttribute('aria-expanded', 'false');
      alphaPop.classList.remove('open');
      closeDatePop();
      hideTip();
    }
  });
  loadCycle(); // 面板一加载就拉一次情绪周期数据；404 时整块降级为占位
  setInterval(pollLatest, 60000);

  // ===== 开机 / 关机 / 重启（浮窗右上角，和 DSH 的那两个键同位置同观感） =====
  // 关机 = 只关面板窗口，8765 采集服务照常跑（数据接口不受影响）；重启 = 真重启 panel_server。
  const serverBase = (typeof window.__DSH_LONGTOU_SERVER === 'string') ? window.__DSH_LONGTOU_SERVER : '';

  panel.querySelector('#dsh-longtou-power').addEventListener('click', () => {
    hideTip();
    settingsPop.classList.remove('open');
    alphaPop.classList.remove('open');
    closeDatePop();
    panel.classList.remove('open');
    // 独立 app 窗口里尝试真把窗口关掉；浏览器不让关就退化成「已关机」死屏
    try { window.close(); } catch (_) { /* 忽略：非脚本打开的窗口关不掉 */ }
    setTimeout(() => {
      if (window.closed) return;
      document.body.innerHTML = '<div style="position:fixed;inset:0;display:flex;align-items:center;'
        + 'justify-content:center;flex-direction:column;gap:8px;background:#0b0b0e;color:#8b8b96;'
        + 'font:14px/1.7 system-ui,-apple-system,sans-serif"><div>面板已关闭</div>'
        + '<div style="font-size:12px;color:#5f5f6b">采集服务仍在运行 · 127.0.0.1:8765</div>'
        + '<button id="lt-power-back" type="button" style="margin-top:8px;padding:6px 16px;cursor:pointer;'
        + 'border:1px solid #2a2a33;border-radius:8px;background:#17171d;color:#e6e6ea">重新打开</button></div>';
      const back = document.getElementById('lt-power-back');
      if (back) back.addEventListener('click', () => location.reload());
    }, 150);
  });

  // ===== 重启：面板收起 → 全屏遮罩 → 轮询 /health 拿到新 started → reload（= 窗口重新打开） =====
  // 旧实现靠「先掉线再上线」猜：新实例 0.6 秒内就接手，400ms 轮询几乎看不到断线，必然 25 秒后误报超时；
  // 且 mode:'no-cors' 拿到的是 opaque response，body 恒为空、读不到任何状态。现在改成确定性比对 + 可见动画。
  const RESTART_TIMEOUT_MS = 25000;
  const RESTART_POLL_MS = 400;
  const RESTART_CLOSED_MIN_MS = 900; // 「关闭态」（点击 → reload）最短总时长，别一闪而过
  const RESTART_VEIL_MIN_MS = 400; // 拿到新 started 后遮罩至少再停 400ms

  function prefersReducedMotion() {
    try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (_) { return false; }
  }

  async function readHealth() {
    // 不能用 no-cors：opaque response 的 body 恒为空，读不到 started/pid；必须走同源正常请求。
    const response = await fetch(serverBase + '/health?t=' + Date.now(), { cache: 'no-store' });
    const text = await response.text();
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return { data: JSON.parse(text), text };
  }

  function setRestartButtonIdle() {
    const btn = panel.querySelector('#dsh-longtou-restart');
    if (!btn) return;
    btn.disabled = false;
    btn.title = '重启面板服务';
  }

  function clearRestartUi() {
    panel.classList.remove('lt-restart-closing');
    const veil = document.getElementById('dsh-longtou-restart-veil');
    if (veil) veil.remove();
    setRestartButtonIdle();
    restarting = false;
  }

  function openRestartVeil() {
    const veil = document.createElement('div');
    veil.id = 'dsh-longtou-restart-veil';
    const ring = document.createElement('div');
    ring.className = 'lt-veil-ring';
    const title = document.createElement('div');
    title.className = 'lt-veil-title';
    title.textContent = '面板服务重启中…';
    const sub = document.createElement('div');
    sub.className = 'lt-veil-sub';
    sub.textContent = '等待服务恢复 0s';
    const actions = document.createElement('div');
    actions.className = 'lt-veil-actions';
    veil.append(ring, title, sub, actions);
    (document.body || document.documentElement).appendChild(veil);
    void veil.offsetWidth; // 先落一帧初始 opacity:0，过渡才会真正播放
    veil.classList.add('lt-veil-in');
    return { sub, actions };
  }

  // reload 后播放「重新打开」入场动画：标记由重启成功分支写入 sessionStorage
  function playRestartEnter() {
    let reopen = false;
    try { reopen = sessionStorage.getItem('lt_restart_reopen') === '1'; } catch (_) { reopen = false; }
    if (!reopen) return;
    try { sessionStorage.removeItem('lt_restart_reopen'); } catch (_) { /* ignore */ }
    panel.classList.remove('lt-restart-enter');
    void panel.offsetWidth;
    panel.classList.add('lt-restart-enter');
    setTimeout(() => panel.classList.remove('lt-restart-enter'), 500);
  }

  let restarting = false;

  async function runRestart() {
    const btn = panel.querySelector('#dsh-longtou-restart');
    if (restarting || !btn) return;
    restarting = true;
    btn.disabled = true;
    btn.title = '重启中…';
    const clickedAt = Date.now();
    let oldKey = null;
    let lastRaw = '（没有拿到 /health 响应）';
    try {
      const before = await readHealth();
      lastRaw = before.text;
      oldKey = String(before.data.started) + '#' + String(before.data.pid);
    } catch (_) { oldKey = null; }
    // 1) 面板收起动画（transform-origin:center，见 CSS）
    panel.classList.add('lt-restart-closing');
    // 2) 淡出后再盖遮罩，用户才看得到「面板关掉了」这一下
    await new Promise((resolve) => setTimeout(resolve, prefersReducedMotion() ? 0 : 220));
    const { sub, actions } = openRestartVeil();
    const ticker = setInterval(() => {
      sub.textContent = '等待服务恢复 ' + ((Date.now() - clickedAt) / 1000).toFixed(1) + 's';
    }, 100);
    // 3) 真正重启：老进程会先把自己杀掉，POST 可能断连，正常
    try {
      await fetch(serverBase + '/restart', { method: 'POST', cache: 'no-store' });
    } catch (_) { /* ignore */ }
    // 4) 轮询 /health：started（或 pid）变了才算新进程起来了
    let newHealth = null;
    const deadline = clickedAt + RESTART_TIMEOUT_MS;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, RESTART_POLL_MS));
      try {
        const now = await readHealth();
        lastRaw = now.text;
        const key = String(now.data.started) + '#' + String(now.data.pid);
        if (now.data.ok === true && key !== oldKey) { newHealth = now.data; break; }
      } catch (_) { /* 新实例还没起来 */ }
    }
    clearInterval(ticker);
    if (newHealth) {
      // 5) 成功：遮罩再停一会儿（≥400ms，且整体关闭态 ≥900ms），然后 reload = 窗口重开
      const holdMs = Math.max(RESTART_VEIL_MIN_MS, RESTART_CLOSED_MIN_MS - (Date.now() - clickedAt));
      sub.textContent = '服务已恢复（pid ' + newHealth.pid + '）· 正在重新打开面板…';
      await new Promise((resolve) => setTimeout(resolve, holdMs));
      try { sessionStorage.setItem('lt_restart_reopen', '1'); } catch (_) { /* ignore */ }
      location.reload();
      return;
    }
    // 6) 失败：遮罩上给原因 + 最后一次原始响应，提供重试/关闭，不弹 alert 也不白屏
    sub.textContent = '25 秒内没拿到新的 started（pid/started 未变化）。最后 /health 返回：'
      + String(lastRaw).slice(0, 200);
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'lt-btn';
    retry.textContent = '重试';
    retry.addEventListener('click', () => { clearRestartUi(); runRestart(); });
    const dismiss = document.createElement('button');
    dismiss.type = 'button';
    dismiss.className = 'lt-btn';
    dismiss.textContent = '关闭遮罩';
    dismiss.addEventListener('click', clearRestartUi);
    actions.append(retry, dismiss);
  }

  panel.querySelector('#dsh-longtou-restart').addEventListener('click', () => { runRestart(); });

  panel.querySelector('#dsh-longtou-close').addEventListener('click', () => {
    hideTip();
    settingsPop.classList.remove('open');
    alphaPop.classList.remove('open');
    closeDatePop();
    panel.classList.remove('open');
  });
  document.addEventListener('scroll', hideTip, true);
  window.addEventListener('resize', hideTip);
  // 日期面板是 fixed：窗口尺寸变化时按按钮新位置重定位（面板本身不移动，无需重建内容）
  window.addEventListener('resize', () => { positionDatePop(); });

  if (window.__DSH_LONGTOU_AUTO_OPEN) {
    setTimeout(() => {
      if (!panel.classList.contains('open')) fab.click();
    }, 260);
  }

  // 进入面板时的一次性自动补刷：延后 1.2 秒再跑，不阻塞首屏；/refresh.json 缺失时内部静默跳过，
  // 面板照常渲染（该请求失败只写 console.warn，不影响任何既有区块）。
  setTimeout(() => {
    autoCatchup().catch((error) => console.warn('[longtou] 自动补刷异常', error));
  }, 1200);
})();

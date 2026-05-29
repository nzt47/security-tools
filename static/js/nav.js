// ════════════════════════════════════════════════════════════
// 灵犀 · 左侧导航栏交互
// ════════════════════════════════════════════════════════════

function initNav() {
  const nav = document.getElementById('nav');
  if (!nav) return;

  // 折叠/展开
  const toggleBtn = document.getElementById('nav-toggle-btn');
  if (toggleBtn) {
    toggleBtn.addEventListener('click', () => {
      const collapsed = nav.classList.toggle('collapsed');
      document.getElementById('app').classList.toggle('nav-collapsed', collapsed);
      app.setState('navCollapsed', collapsed);
      toggleBtn.textContent = collapsed ? '▶' : '◀';
    });
  }

  // 点击导航项
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const view = btn.dataset.view;
      if (!view) return;
      if (view === 'settings') {
        showSettings();
        return;
      }
      if (view === 'refresh') {
        refreshAll();
        return;
      }
      app.switchView(view);
    });
  });

  // 窗口 resize 自动折叠
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (window.innerWidth < 768) {
        nav.classList.add('collapsed');
        document.getElementById('app').classList.add('nav-collapsed');
      } else if (!app.state.navCollapsed) {
        nav.classList.remove('collapsed');
        document.getElementById('app').classList.remove('nav-collapsed');
      }
    }, 200);
  });
}

function refreshAll() {
  app.emit('refresh');
}

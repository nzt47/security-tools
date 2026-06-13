/**
 * Mascot 点击交互功能自动化测试脚本
 * 用法：在浏览器控制台（F12）中粘贴此代码并执行
 */

(function() {
  console.log('🎭 开始 Mascot 点击交互测试...');

  const results = {
    passed: 0,
    failed: 0,
    errors: []
  };

  function test(name, condition, errorMsg) {
    if (condition) {
      console.log(`✅ ${name}`);
      results.passed++;
    } else {
      console.error(`❌ ${name}: ${errorMsg}`);
      results.failed++;
      results.errors.push({ name, errorMsg });
    }
  }

  // 1. 检查 Mascot 元素是否存在
  const mascot = document.getElementById('mascot');
  test('Mascot 容器存在', mascot !== null, '未找到 id="mascot" 的元素');

  const mascotArea = document.getElementById('mascot-area');
  test('Mascot 区域存在', mascotArea !== null, '未找到 id="mascot-area" 的元素');

  // 2. 检查眼睛元素
  const leftEye = document.getElementById('eye-left');
  const rightEye = document.getElementById('eye-right');
  test('左眼存在', leftEye !== null, '未找到 id="eye-left" 的元素');
  test('右眼存在', rightEye !== null, '未找到 id="eye-right" 的元素');

  // 3. 检查嘴巴元素
  const mouth = document.getElementById('mascot-mouth');
  test('嘴巴存在', mouth !== null, '未找到 id="mascot-mouth" 的元素');

  // 4. 检查状态元素
  const status = document.getElementById('mascot-status');
  test('状态文字存在', status !== null, '未找到 id="mascot-status" 的元素');

  // 5. 检查光晕元素
  const glow = document.getElementById('mascot-glow');
  test('光晕存在', glow !== null, '未找到 id="mascot-glow" 的元素');

  // 6. 检查全局变量
  test('__mascotMood 全局变量', typeof __mascotMood !== 'undefined', '__mascotMood 未定义');
  test('__mascotTargetLookAt 全局变量', typeof __mascotTargetLookAt !== 'undefined', '__mascotTargetLookAt 未定义');
  test('__mascotCurrentLookAt 全局变量', typeof __mascotCurrentLookAt !== 'undefined', '__mascotCurrentLookAt 未定义');

  // 7. 检查函数是否存在
  test('mascotClick 函数', typeof mascotClick === 'function', 'mascotClick 函数未定义');
  test('mascotHover 函数', typeof mascotHover === 'function', 'mascotHover 函数未定义');
  test('setMascotMood 函数', typeof setMascotMood === 'function', 'setMascotMood 函数未定义');
  test('initMascotTracking 函数', typeof initMascotTracking === 'function', 'initMascotTracking 函数未定义');
  test('updateEyePosition 函数', typeof updateEyePosition === 'function', 'updateEyePosition 函数未定义');

  // 8. 测试点击交互
  console.log('\n🔘 测试点击交互...');
  const initialMood = typeof __mascotMood !== 'undefined' ? __mascotMood : null;
  const initialStatus = status ? status.textContent : null;

  if (typeof mascotClick === 'function') {
    mascotClick();
    setTimeout(() => {
      const newMood = typeof __mascotMood !== 'undefined' ? __mascotMood : null;
      test('点击后情绪变化', newMood === 'idle', `点击后情绪应为 'idle'，实际为 '${newMood}'`);
      console.log(`   初始状态: ${initialMood} → 点击后状态: ${newMood}`);
    }, 2000);
  }

  // 9. 测试 Hover 交互
  console.log('\n🖱️ 测试 Hover 交互...');
  if (typeof mascotHover === 'function' && mascot) {
    mascotHover(true);
    test('Hover 时添加 hovered 类', mascot.classList.contains('mascot-hovered'), 'Hover 后应添加 mascot-hovered 类');

    mascotHover(false);
    test('离开 Hover 时移除 hovered 类', !mascot.classList.contains('mascot-hovered'), '离开 Hover 后应移除 mascot-hovered 类');
  }

  // 10. 测试视线追踪
  console.log('\n👀 测试视线追踪...');
  if (typeof updateEyePosition === 'function') {
    const initialTransform = leftEye ? leftEye.style.transform : '';
    updateEyePosition(0.5, 0.5);
    const newTransform = leftEye ? leftEye.style.transform : '';

    test('视线追踪位置更新', initialTransform !== newTransform, `眼睛位置未更新: ${initialTransform} → ${newTransform}`);

    // 重置
    updateEyePosition(0, 0);
  }

  // 11. 测试视线追踪初始化
  console.log('\n⚙️ 测试视线追踪初始化...');
  if (typeof initMascotTracking === 'function') {
    const initialAnimationId = typeof __mascotAnimationId !== 'undefined' ? __mascotAnimationId : null;
    initMascotTracking();
    const newAnimationId = typeof __mascotAnimationId !== 'undefined' ? __mascotAnimationId : null;
    test('视线追踪动画启动', initialAnimationId !== newAnimationId, '动画 ID 未改变，可能未正常启动');
  }

  // 输出测试结果
  console.log('\n========================================');
  console.log('📊 测试结果汇总');
  console.log(`✅ 通过: ${results.passed}`);
  console.log(`❌ 失败: ${results.failed}`);
  console.log('========================================');

  if (results.failed > 0) {
    console.log('\n失败项目:');
    results.errors.forEach(err => {
      console.log(`  - ${err.name}: ${err.errorMsg}`);
    });
  }

  // 返回测试结果对象
  return {
    results,
    getState: () => ({
      mood: typeof __mascotMood !== 'undefined' ? __mascotMood : null,
      targetLookAt: typeof __mascotTargetLookAt !== 'undefined' ? __mascotTargetLookAt : null,
      currentLookAt: typeof __mascotCurrentLookAt !== 'undefined' ? __mascotCurrentLookAt : null,
      animationId: typeof __mascotAnimationId !== 'undefined' ? __mascotAnimationId : null,
      isHovered: mascot ? mascot.classList.contains('mascot-hovered') : null
    }),
    triggerClick: () => {
      if (typeof mascotClick === 'function') {
        mascotClick();
        console.log('🔘 已触发点击事件');
      }
    },
    triggerHover: (isHovered) => {
      if (typeof mascotHover === 'function') {
        mascotHover(isHovered);
        console.log(`🖱️ 已触发 Hover 事件: ${isHovered}`);
      }
    }
  };
})();

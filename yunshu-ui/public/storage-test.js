/**
 * 存储验证测试脚本
 * 运行方式：在浏览器控制台中粘贴此代码
 */

// 1. 打印当前存储配置
console.log('='.repeat(60));
console.log('【测试1】检查存储配置');
console.log('='.repeat(60));

console.log('STORAGE_CONFIG.type:', STORAGE_CONFIG.type);
console.log('当前存储适配器:', STORAGE_CONFIG.type === 'localStorage' ? 'localStorage' : 'sessionStorage');

// 2. 检查存储的 key
console.log('\n' + '='.repeat(60));
console.log('【测试2】检查已保存的 key');
console.log('='.repeat(60));

const storage = STORAGE_CONFIG.type === 'localStorage' ? localStorage : sessionStorage;
console.log('存储类型:', storage === localStorage ? 'localStorage' : 'sessionStorage');
console.log('Yunshu_mood:', storage.getItem('Yunshu_mood'));
console.log('Yunshu_messages:', storage.getItem('Yunshu_messages') ? '已保存' : '未保存');

// 3. 手动测试 sessionStorage 清除
console.log('\n' + '='.repeat(60));
console.log('【测试3】sessionStorage 清除测试');
console.log('='.repeat(60));

// 先切换到 sessionStorage
STORAGE_CONFIG.type = 'sessionStorage';
const sessionStorage2 = sessionStorage;

// 保存测试数据
sessionStorage2.setItem('test_key', 'test_value');
console.log('已保存测试数据: test_key = test_value');
console.log('读取: ', sessionStorage2.getItem('test_key'));

// 清除
sessionStorage2.removeItem('test_key');
console.log('已清除');
console.log('读取: ', sessionStorage2.getItem('test_key'));

// 恢复配置
STORAGE_CONFIG.type = 'localStorage';

// 4. 存储数据示例
console.log('\n' + '='.repeat(60));
console.log('【测试4】保存测试数据到当前存储');
console.log('='.repeat(60));

const currentStorage = STORAGE_CONFIG.type === 'localStorage' ? localStorage : sessionStorage;
currentStorage.setItem('Yunshu_test', JSON.stringify({
  mood: 'happy',
  timestamp: new Date().toISOString(),
  message: '这是一条测试数据'
}));
console.log('已保存测试数据到:', STORAGE_CONFIG.type);
console.log('数据:', currentStorage.getItem('Yunshu_test'));

// 5. 模拟刷新
console.log('\n' + '='.repeat(60));
console.log('【测试5】sessionStorage 刷新测试说明');
console.log('='.repeat(60));
console.log('⚠️  要测试 sessionStorage 是否在刷新后清除：');
console.log('   1. 点击"会话"按钮切换到 sessionStorage 模式');
console.log('   2. 运行自动化测试或发送消息');
console.log('   3. 观察控制台，确认数据已保存');
console.log('   4. 按 F5 刷新页面');
console.log('   5. 观察数据是否保留（sessionStorage 应该保留）');
console.log('   6. 关闭当前标签页，重新打开');
console.log('   7. 观察数据是否清除（sessionStorage 应该清除）');

// 6. 路由参数说明
console.log('\n' + '='.repeat(60));
console.log('【测试6】路由参数存储类型说明');
console.log('='.repeat(60));
console.log('💡  路由参数方案：');
console.log('   URL 格式: http://localhost:5173/?storage=localStorage');
console.log('   URL 格式: http://localhost:5173/?storage=sessionStorage');
console.log('   这样可以：');
console.log('   - 分享链接时指定存储类型');
console.log('   - 通过书签保存特定配置');
console.log('   - 方便调试不同存储模式');

// 获取当前 URL 参数
const urlParams = new URLSearchParams(window.location.search);
const storageParam = urlParams.get('storage');
console.log('\n当前 URL 参数: storage =', storageParam || '(未设置，使用默认)');

console.log('\n' + '='.repeat(60));
console.log('✅ 验证测试完成！请查看上方输出');
console.log('='.repeat(60));

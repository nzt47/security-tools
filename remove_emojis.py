#!/usr/bin/env python3
"""移除文件中的emoji字符"""
import sys
import re

# emoji替换映射
emoji_map = {
    '\U0001f680': '[START]',  # 火箭
    '\U0001f389': '[PARTY]',  # 派对
    '\U0001f6ab': '[NO]',     # 禁止
    '\U0001f7e2': '[OK]',     # 绿色勾
    '\U0001f7e1': '[WARN]',   # 黄色警告
    '\U0001f535': '[BLUE]',   # 蓝色
    '\U0001f534': '[RED]',    # 红色
    '\U0001f7e0': '[ORANGE]', # 橙色
    '\U0001f504': '[RELOAD]', # 刷新
    '\U0001f3af': '[TARGET]', # 目标
    '\U0001f4cb': '[LIST]',  # 列表
    '\U0001f4e6': '[PACKAGE]',# 包
    '\U0001f4dd': '[NOTE]',  # 笔记
    '\U0001f4ac': '[SPEECH]',# 说话
    '\U0001f4d6': '[BOOK]',  # 书
    '\U0001f4c5': '[CALENDAR]',# 日历
    '\U0001f4f1': '[PHONE]', # 手机
    '\U0001f4e7': '[MAIL]',  # 邮件
    '\U0001f4f0': '[NEWSPAPER]',# 报纸
    '\U0001f3db': '[MEMO]',  # 备忘录
    '\U0001f4c1': '[FOLDER]',# 文件夹
    '\U0001f4c2': '[FILE]',  # 文件
    '\U0001f4da': '[DOCS]',  # 文档
    '\U0001f4d5': '[MANUAL]',# 手册
    '\U0001f4d2': '[PAD]',   # 写字板
    '\U0001f4d1': '[CHECK]',  # 检查
    '\U0001f4d8': '[DRAFT]', # 草稿
    '\U0001f4d9': '[DONE]',  # 完成
    '\U0001f4d4': '[PPT]',    # 演示
    '\U0001f4d3': '[NOTEBOOK]',# 笔记本
    '\U0001f4d7': '[TEXTBOOK]',# 教科书
    '\U0001f4d0': '[CLIP]',  # 剪贴板
    '\U0001f3ac': '[CLAPPER]',# 场记板
    '\U0001f4fa': '[DVD]',   # DVD
    '\U0001f4fb': '[FLOPPY]',# 软盘
    '\U0001f4fc': '[VIDEOCASSETTE]',# 录像带
    '\U0001f50a': '[SPEAKER]',# 扬声器
    '\U0001f508': '[MUTE]',  # 静音
    '\U0001f507': '[SOUND]', # 声音
    '\U0001f509': '[SPEAKER2]',# 喇叭2
    '\U0001f506': '[SPEAKER3]',# 喇叭3
    '\U0001f505': '[SPEAKER4]',# 喇叭4
    '\U0001f4e2': '[ANNOUNCEMENT]',# 公告
    '\U0001f4e3': '[HANDSHAKE]',# 握手
    '\U0001f4e4': '[OUTTRAY]',# 出队
    '\U0001f4e5': '[INTTRAY]',# 入队
    '\U0001f4e8': '[POSTBOX]',# 邮箱
    '\U0001f4e9': '[POSTAL]',# 邮政
    '\U0001f4ea': '[MAILBOX]',# 信箱
    '\U0001f4eb': '[MAILBOX2]',# 信箱2
    '\U0001f4ec': '[MAILBOX3]',# 信箱3
    '\U0001f4ed': '[MAILBOX4]',# 信箱4
    '\U0001f4ee': '[POSTBOX5]',# 邮政5
    '\U0001f4ef': '[POSTBOX6]',# 邮政6
    '\U0001f4f2': '[BELL]',  # 铃铛
    '\U0001f514': '[BELL2]',  # 铃铛2
    '\U0001f515': '[BELL3]',  # 铃铛3
    '\U0001f516': '[BELL4]',  # 铃铛4
    '\U0001f517': '[LINK]',   # 链接
    '\U0001f518': '[RADIO]',  # 无线电
    '\U0001f519': '[BACK]',   # 返回
    '\U0001f51a': '[END]',    # 结束
    '\U0001f51b': '[ON]',     # 开启
    '\U0001f51c': '[SOON]',   # 即将
    '\U0001f51d': '[TOP]',    # 顶部
    '\U0001f51e': '[UP]',     # 向上
    '\U0001f51f': '[DOWN]',   # 向下
    '\U0001f520': '[BACK2]',   # 返回2
    '\U0001f521': '[UNLOCK]', # 解锁
    '\U0001f522': '[LOCK]',   # 锁定
    '\U0001f523': '[KEY]',    # 钥匙
    '\U0001f524': '[SEARCH]', # 搜索
    '\U0001f525': '[FIRE]',   # 火
    '\U0001f526': '[FLASHLIGHT]',# 手电筒
    '\U0001f527': '[WRENCH]', # 扳手
    '\U0001f528': '[HAMMER]', # 锤子
    '\U0001f529': '[NUT]',    # 螺母
    '\U0001f52a': '[DROP]',   # 滴
    '\U0001f52b': '[GUN]',    # 枪
    '\U0001f52c': '[MICROSCOPE]',# 显微镜
    '\U0001f52d': '[TELESCOPE]',# 望远镜
    '\U0001f52e': '[CRYSTAL]',# 水晶球
    '\U0001f52f': '[CRYSTAL2]',# 水晶球2
    '\U0001f530': '[COMPASS]',# 指南针
    '\U0001f531': '[UMBRELLA]',# 雨伞
    '\U0001f532': '[BALL]',   # 球
    '\U0001f533': '[GLOBE]',  # 地球
    '\U0001f534': '[MAP]',    # 地图
    '\U0001f535': '[MAP2]',   # 地图2
    '\U0001f536': '[MAP3]',   # 地图3
    '\U0001f537': '[MAP4]',   # 地图4
    '\U0001f538': '[MAP5]',   # 地图5
    '\U0001f539': '[MAP6]',   # 地图6
    '\U0001f53a': '[MOUNT]',  # 山
    '\U0001f53b': '[BEACH]',  # 海滩
    '\U0001f53c': '[ISLAND]', # 岛屿
    '\U0001f53d': '[PARK]',   # 公园
    '\U0001f53e': '[ROAD]',   # 道路
    '\U0001f53f': '[STOP]',   # 停止
    '\U0001f540': '[TRAM]',   # 有轨电车
    '\U0001f541': '[TRAM2]',  # 有轨电车2
    '\U0001f542': '[MINIATURE]',# 微缩
    '\U0001f543': '[MINIATURE2]',# 微缩2
    '\U0001f544': '[MOOD]',    # 心情
    '\U0001f545': '[MOOD2]',   # 心情2
    '\U0001f546': '[MOOD3]',   # 心情3
    '\U0001f547': '[MOOD4]',   # 心情4
    '\U0001f548': '[MOOD5]',   # 心情5
    '\U0001f549': '[MOOD6]',   # 心情6
    '\U0001f54a': '[MOOD7]',   # 心情7
    '\U0001f54b': '[MOOD8]',   # 心情8
    '\U0001f54c': '[MOOD9]',   # 心情9
    '\U0001f54d': '[MOOD10]',  # 心情10
    '\U0001f54e': '[MOOD11]',  # 心情11
    '\U0001f54f': '[MOOD12]',  # 心情12
    '\U0001f550': '[CLOCK]',  # 时钟
    '\U0001f551': '[CLOCK2]', # 时钟2
    '\U0001f552': '[CLOCK3]', # 时钟3
    '\U0001f553': '[CLOCK4]', # 时钟4
    '\U0001f554': '[CLOCK5]', # 时钟5
    '\U0001f555': '[CLOCK6]', # 时钟6
    '\U0001f556': '[CLOCK7]', # 时钟7
    '\U0001f557': '[CLOCK8]', # 时钟8
    '\U0001f558': '[CLOCK9]', # 时钟9
    '\U0001f559': '[CLOCK10]',# 时钟10
    '\U0001f55a': '[CLOCK11]',# 时钟11
    '\U0001f55b': '[CLOCK12]',# 时钟12
    '\U0001f55c': '[CLOCK13]',# 时钟13
    '\U0001f55d': '[CLOCK14]',# 时钟14
    '\U0001f55e': '[CLOCK15]',# 时钟15
    '\U0001f55f': '[CLOCK16]',# 时钟16
    '\U0001f560': '[CLOCK17]',# 时钟17
    '\U0001f561': '[CLOCK18]',# 时钟18
    '\U0001f562': '[CLOCK19]',# 时钟19
    '\U0001f563': '[CLOCK20]',# 时钟20
    '\U0001f564': '[CLOCK21]',# 时钟21
    '\U0001f565': '[CLOCK22]',# 时钟22
    '\U0001f566': '[CLOCK23]',# 时钟23
    '\U0001f567': '[CLOCK24]',# 时钟24
    '\U0001f568': '[CLOCK25]',# 时钟25
    '\U0001f569': '[CLOCK26]',# 时钟26
    '\U0001f56a': '[CLOCK27]',# 时钟27
    '\U0001f56b': '[CLOCK28]',# 时钟28
    '\U0001f56c': '[CLOCK29]',# 时钟29
    '\U0001f56d': '[CLOCK30]',# 时钟30
    '\U0001f56e': '[CLOCK31]',# 时钟31
    '\U0001f56f': '[CLOCK32]',# 时钟32
    '\U0001f570': '[CLOCK33]',# 时钟33
    '\U0001f571': '[CLOCK34]',# 时钟34
    '\U0001f572': '[CLOCK35]',# 时钟35
    '\U0001f573': '[CLOCK36]',# 时钟36
    '\U0001f574': '[CLOCK37]',# 时钟37
    '\U0001f575': '[CLOCK38]',# 时钟38
    '\U0001f576': '[CLOCK39]',# 时钟39
    '\U0001f577': '[CLOCK40]',# 时钟40
    '\U0001f578': '[CLOCK41]',# 时钟41
    '\U0001f579': '[CLOCK42]',# 时钟42
    '\U0001f57a': '[CLOCK43]',# 时钟43
    '\U0001f57b': '[CLOCK44]',# 时钟44
    '\U0001f57c': '[CLOCK45]',# 时钟45
    '\U0001f57d': '[CLOCK46]',# 时钟46
    '\U0001f57e': '[CLOCK47]',# 时钟47
    '\U0001f57f': '[CLOCK48]',# 时钟48
    '\U0001f580': '[CLOCK49]',# 时钟49
    '\U0001f581': '[CLOCK50]',# 时钟50
    '\U0001f582': '[CLOCK51]',# 时钟51
    '\U0001f583': '[CLOCK52]',# 时钟52
    '\U0001f584': '[CLOCK53]',# 时钟53
    '\U0001f585': '[CLOCK54]',# 时钟54
    '\U0001f586': '[CLOCK55]',# 时钟55
    '\U0001f587': '[CLOCK56]',# 时钟56
    '\U0001f588': '[CLOCK57]',# 时钟57
    '\U0001f589': '[CLOCK58]',# 时钟58
    '\U0001f58a': '[CLOCK59]',# 时钟59
    '\U0001f58b': '[CLOCK60]',# 时钟60
    '\U0001f58c': '[CLOCK61]',# 时钟61
    '\U0001f58d': '[CLOCK62]',# 时钟62
    '\U0001f58e': '[CLOCK63]',# 时钟63
    '\U0001f58f': '[CLOCK64]',# 时钟64
    '\U0001f590': '[CLOCK65]',# 时钟65
    '\U0001f591': '[CLOCK66]',# 时钟66
    '\U0001f592': '[CLOCK67]',# 时钟67
    '\U0001f593': '[CLOCK68]',# 时钟68
    '\U0001f594': '[CLOCK69]',# 时钟69
    '\U0001f595': '[CLOCK70]',# 时钟70
    '\U0001f596': '[CLOCK71]',# 时钟71
    '\U0001f597': '[CLOCK72]',# 时钟72
    '\U0001f598': '[CLOCK73]',# 时钟73
    '\U0001f599': '[CLOCK74]',# 时钟74
    '\U0001f59a': '[CLOCK75]',# 时钟75
    '\U0001f59b': '[CLOCK76]',# 时钟76
    '\U0001f59c': '[CLOCK77]',# 时钟77
    '\U0001f59d': '[CLOCK78]',# 时钟78
    '\U0001f59e': '[CLOCK79]',# 时钟79
    '\U0001f59f': '[CLOCK80]',# 时钟80
    '\U0001f5a0': '[CLOCK81]',# 时钟81
    '\U0001f5a1': '[CLOCK82]',# 时钟82
    '\U0001f5a2': '[CLOCK83]',# 时钟83
    '\U0001f5a3': '[CLOCK84]',# 时钟84
    '\U0001f5a4': '[CLOCK85]',# 时钟85
    '\U0001f5a5': '[CLOCK86]',# 时钟86
    '\U0001f5a6': '[CLOCK87]',# 时钟87
    '\U0001f5a7': '[CLOCK88]',# 时钟88
    '\U0001f5a8': '[CLOCK89]',# 时钟89
    '\U0001f5a9': '[CLOCK90]',# 时钟90
    '\U0001f5aa': '[CLOCK91]',# 时钟91
    '\U0001f5ab': '[CLOCK92]',# 时钟92
    '\U0001f5ac': '[CLOCK93]',# 时钟93
    '\U0001f5ad': '[CLOCK94]',# 时钟94
    '\U0001f5ae': '[CLOCK95]',# 时钟95
    '\U0001f5af': '[CLOCK96]',# 时钟96
    '\U0001f5b0': '[CLOCK97]',# 时钟97
    '\U0001f5b1': '[CLOCK98]',# 时钟98
    '\U0001f5b2': '[CLOCK99]',# 时钟99
    '\U0001f5b3': '[CLOCK100]',# 时钟100
    '\U0001f5b4': '[CLOCK101]',# 时钟101
    '\U0001f5b5': '[CLOCK102]',# 时钟102
    '\U0001f5b6': '[CLOCK103]',# 时钟103
    '\U0001f5b7': '[CLOCK104]',# 时钟104
    '\U0001f5b8': '[CLOCK105]',# 时钟105
    '\U0001f5b9': '[CLOCK106]',# 时钟106
    '\U0001f5ba': '[CLOCK107]',# 时钟107
    '\U0001f5bb': '[CLOCK108]',# 时钟108
    '\U0001f5bc': '[CLOCK109]',# 时钟109
    '\U0001f5bd': '[CLOCK110]',# 时钟110
    '\U0001f5be': '[CLOCK111]',# 时钟111
    '\U0001f5bf': '[CLOCK112]',# 时钟112
    '\U0001f5c0': '[CLOCK113]',# 时钟113
    '\U0001f5c1': '[CLOCK114]',# 时钟114
    '\U0001f5c2': '[CLOCK115]',# 时钟115
    '\U0001f5c3': '[CLOCK116]',# 时钟116
    '\U0001f5c4': '[CLOCK117]',# 时钟117
    '\U0001f5c5': '[CLOCK118]',# 时钟118
    '\U0001f5c6': '[CLOCK119]',# 时钟119
    '\U0001f5c7': '[CLOCK120]',# 时钟120
    '\U0001f5c8': '[CLOCK121]',# 时钟121
    '\U0001f5c9': '[CLOCK122]',# 时钟122
    '\U0001f5ca': '[CLOCK123]',# 时钟123
    '\U0001f5cb': '[CLOCK124]',# 时钟124
    '\U0001f5cc': '[CLOCK125]',# 时钟125
    '\U0001f5cd': '[CLOCK126]',# 时钟126
    '\U0001f5ce': '[CLOCK127]',# 时钟127
    '\U0001f5cf': '[CLOCK128]',# 时钟128
    '\U0001f5d0': '[CLOCK129]',# 时钟129
    '\U0001f5d1': '[CLOCK130]',# 时钟130
    '\U0001f5d2': '[CLOCK131]',# 时钟131
    '\U0001f5d3': '[CLOCK132]',# 时钟132
    '\U0001f5d4': '[CLOCK133]',# 时钟133
    '\U0001f5d5': '[CLOCK134]',# 时钟134
    '\U0001f5d6': '[CLOCK135]',# 时钟135
    '\U0001f5d7': '[CLOCK136]',# 时钟136
    '\U0001f5d8': '[CLOCK137]',# 时钟137
    '\U0001f5d9': '[CLOCK138]',# 时钟138
    '\U0001f5da': '[CLOCK139]',# 时钟139
    '\U0001f5db': '[CLOCK140]',# 时钟140
    '\U0001f5dc': '[CLOCK141]',# 时钟141
    '\U0001f5dd': '[CLOCK142]',# 时钟142
    '\U0001f5de': '[CLOCK143]',# 时钟143
    '\U0001f5df': '[CLOCK144]',# 时钟144
    '\U0001f5e0': '[CLOCK145]',# 时钟145
    '\U0001f5e1': '[CLOCK146]',# 时钟146
    '\U0001f5e2': '[CLOCK147]',# 时钟147
    '\U0001f5e3': '[CLOCK148]',# 时钟148
    '\U0001f5e4': '[CLOCK149]',# 时钟149
    '\U0001f5e5': '[CLOCK150]',# 时钟150
    '\U0001f5e6': '[CLOCK151]',# 时钟151
    '\U0001f5e7': '[CLOCK152]',# 时钟152
    '\U0001f5e8': '[CLOCK153]',# 时钟153
    '\U0001f5e9': '[CLOCK154]',# 时钟154
    '\U0001f5ea': '[CLOCK155]',# 时钟155
    '\U0001f5eb': '[CLOCK156]',# 时钟156
    '\U0001f5ec': '[CLOCK157]',# 时钟157
    '\U0001f5ed': '[CLOCK158]',# 时钟158
    '\U0001f5ee': '[CLOCK159]',# 时钟159
    '\U0001f5ef': '[CLOCK160]',# 时钟160
    '\U0001f5f0': '[CLOCK161]',# 时钟161
    '\U0001f5f1': '[CLOCK162]',# 时钟162
    '\U0001f5f2': '[CLOCK163]',# 时钟163
    '\U0001f5f3': '[CLOCK164]',# 时钟164
    '\U0001f5f4': '[CLOCK165]',# 时钟165
    '\U0001f5f5': '[CLOCK166]',# 时钟166
    '\U0001f5f6': '[CLOCK167]',# 时钟167
    '\U0001f5f7': '[CLOCK168]',# 时钟168
    '\U0001f5f8': '[CLOCK169]',# 时钟169
    '\U0001f5f9': '[CLOCK170]',# 时钟170
    '\U0001f5fa': '[CLOCK171]',# 时钟171
    '\U0001f5fb': '[CLOCK172]',# 时钟172
    '\U0001f5fc': '[CLOCK173]',# 时钟173
    '\U0001f5fd': '[CLOCK174]',# 时钟174
    '\U0001f5fe': '[CLOCK175]',# 时钟175
    '\U0001f5ff': '[CLOCK176]',# 时钟176
    '\U0001f600': '[GRIN]',   # 咧嘴笑
    '\U0001f601': '[GRIN2]',  # 咧嘴笑2
    '\U0001f602': '[JOY]',    # 笑哭
    '\U0001f603': '[SMILE]',  # 微笑
    '\U0001f604': '[SMILE2]', # 微笑2
    '\U0001f605': '[SMILE3]', # 微笑3
    '\U0001f606': '[SMILE4]', # 微笑4
    '\U0001f607': '[SMILE5]', # 微笑5
    '\U0001f608': '[SMILE6]', # 微笑6
    '\U0001f609': '[SMILE7]', # 微笑7
    '\U0001f60a': '[SMILE8]', # 微笑8
    '\U0001f60b': '[SMILE9]', # 微笑9
    '\U0001f60c': '[SMILE10]',# 微笑10
    '\U0001f60d': '[SMILE11]',# 微笑11
    '\U0001f60e': '[SMILE12]',# 微笑12
    '\U0001f60f': '[SMILE13]',# 微笑13
    '\U0001f610': '[SMILE14]',# 微笑14
    '\U0001f611': '[SMILE15]',# 微笑15
    '\U0001f612': '[SMILE16]',# 微笑16
    '\U0001f613': '[SMILE17]',# 微笑17
    '\U0001f614': '[SMILE18]',# 微笑18
    '\U0001f615': '[SMILE19]',# 微笑19
    '\U0001f616': '[SMILE20]',# 微笑20
    '\U0001f617': '[SMILE21]',# 微笑21
    '\U0001f618': '[SMILE22]',# 微笑22
    '\U0001f619': '[SMILE23]',# 微笑23
    '\U0001f61a': '[SMILE24]',# 微笑24
    '\U0001f61b': '[SMILE25]',# 微笑25
    '\U0001f61c': '[SMILE26]',# 微笑26
    '\U0001f61d': '[SMILE27]',# 微笑27
    '\U0001f61e': '[SMILE28]',# 微笑28
    '\U0001f61f': '[SMILE29]',# 微笑29
    '\U0001f620': '[FROWN]',  # 皱眉
    '\U0001f621': '[ANGER]',  # 愤怒
    '\U0001f622': '[CRY]',    # 哭
    '\U0001f623': '[PERSIST]',# 坚持
    '\U0001f624': '[TRIUMPH]',# 胜利
    '\U0001f625': '[DISAPPOINT]',# 失望
    '\U0001f626': '[DISAPPOINT2]',# 失望2
    '\U0001f627': '[DISAPPOINT3]',# 失望3
    '\U0001f628': '[FEAR]',   # 恐惧
    '\U0001f629': '[FEAR2]',  # 恐惧2
    '\U0001f62a': '[SLEEP]',  # 睡眠
    '\U0001f62b': '[SLEEP2]', # 睡眠2
    '\U0001f62c': '[SLEEP3]', # 睡眠3
    '\U0001f62d': '[SLEEP4]', # 睡眠4
    '\U0001f62e': '[SPEECHLESS]',# 无语
    '\U0001f62f': '[SPEECHLESS2]',# 无语2
    '\U0001f630': '[CONFOUNDED]',# 困惑
    '\U0001f631': '[SCREAM]', # 尖叫
    '\U0001f632': '[ASTONISHED]',# 惊讶
    '\U0001f633': '[FLUSHED]',# 脸红
    '\U0001f634': '[DREAMING]',# 做梦
    '\U0001f635': '[DIZZY]',  # 晕
    '\U0001f636': '[MOUTH]',  # 嘴
    '\U0001f637': '[SICK]',   # 生病
    '\U0001f638': '[CAT_FACE]',# 猫脸
    '\U0001f639': '[CAT_FACE2]',# 猫脸2
    '\U0001f63a': '[CAT_FACE3]',# 猫脸3
    '\U0001f63b': '[CAT_FACE4]',# 猫脸4
    '\U0001f63c': '[CAT_FACE5]',# 猫脸5
    '\U0001f63d': '[CAT_FACE6]',# 猫脸6
    '\U0001f63e': '[CAT_FACE7]',# 猫脸7
    '\U0001f63f': '[CAT_FACE8]',# 猫脸8
    '\U0001f640': '[CAT_FACE9]',# 猫脸9
    '\U0001f641': '[SLIGHT_FROWN]',# 轻微皱眉
    '\U0001f642': '[SLIGHT_SMILE]',# 轻微微笑
    '\U0001f643': '[UPSIDE_DOWN]',# 倒置
    '\U0001f644': '[FACE_ROLLING]',# 翻白眼
    '\U0001f645': '[GESTURE_NO]',# 否定手势
    '\U0001f646': '[GESTURE_OK]',# 确认手势
    '\U0001f647': '[GESTURE_BOW]',# 鞠躬
    '\U0001f648': '[SEE_NO_EVIL]',# 看不到邪恶
    '\U0001f649': '[HEAR_NO_EVIL]',# 听不到邪恶
    '\U0001f64a': '[SPEAK_NO_EVIL]',# 说不邪恶
    '\U0001f64b': '[HAPPY_RAISING]',# 开心举手
    '\U0001f64c': '[RAISING_HANDS]',# 举手
    '\U0001f64d': '[FROWN_PERSON]',# 皱眉的人
    '\U0001f64e': '[FROWN_PERSON2]',# 皱眉的人2
    '\U0001f64f': '[PRAYING]',# 祈祷
    '\U0001f680': '[ROCKET]',  # 火箭
    '\U0001f681': '[ROCKET2]',  # 火箭2
    '\U0001f682': '[HELICOPTER]',# 直升机
    '\U0001f683': '[TRAIN]',   # 火车
    '\U0001f684': '[TRAIN2]',  # 火车2
    '\U0001f685': '[TRAIN3]',  # 火车3
    '\U0001f686': '[TRAIN4]',  # 火车4
    '\U0001f687': '[METRO]',   # 地铁
    '\U0001f688': '[METRO2]',  # 地铁2
    '\U0001f689': '[TRAM]',    # 有轨电车
    '\U0001f68a': '[TRAM2]',   # 有轨电车2
    '\U0001f68b': '[TRAM3]',   # 有轨电车3
    '\U0001f68c': '[BUS]',     # 公共汽车
    '\U0001f68d': '[BUS2]',   # 公共汽车2
    '\U0001f68e': '[BUS3]',   # 公共汽车3
    '\U0001f68f': '[BUS4]',   # 公共汽车4
    '\U0001f690': '[BUS5]',   # 公共汽车5
    '\U0001f691': '[AMBULANCE]',# 救护车
    '\U0001f692': '[FIRE_ENGINE]',# 消防车
    '\U0001f693': '[POLICE_CAR]',# 警车
    '\U0001f694': '[TAXI]',   # 出租车
    '\U0001f695': '[TAXI2]',   # 出租车2
    '\U0001f696': '[CAR]',    # 汽车
    '\U0001f697': '[CAR2]',   # 汽车2
    '\U0001f698': '[CAR3]',   # 汽车3
    '\U0001f699': '[CAR4]',   # 汽车4
    '\U0001f69a': '[TRUCK]',  # 卡车
    '\U0001f69b': '[TRUCK2]', # 卡车2
    '\U0001f69c': '[TRUCK3]', # 卡车3
    '\U0001f69d': '[BIKE]',   # 自行车
    '\U0001f69e': '[BIKE2]',  # 自行车2
    '\U0001f69f': '[BIKE3]',  # 自行车3
    '\U0001f6a0': '[SKATEBOARD]',# 滑板
    '\U0001f6a1': '[SKATEBOARD2]',# 滑板2
    '\U0001f6a2': '[SHIP]',   # 船
    '\U0001f6a3': '[ROWBOAT]',# 划船
    '\U0001f6a4': '[SPEEDBOAT]',# 快艇
    '\U0001f6a5': '[TRAFFIC_LIGHT]',# 红绿灯
    '\U0001f6a6': '[TRAFFIC_LIGHT2]',# 红绿灯2
    '\U0001f6a7': '[CONSTRUCTION]',# 施工
    '\U0001f6a8': '[ROTATING_LIGHT]',# 旋转灯
    '\U0001f6a9': '[STOP_SIGN]',# 停车标志
    '\U0001f6aa': '[DOOR]',   # 门
    '\U0001f6ab': '[NO_ENTRY]',# 禁止进入
    '\U0001f6ac': '[SMOKING]',# 吸烟
    '\U0001f6ad': '[NO_SMOKING]',# 禁止吸烟
    '\U0001f6ae': '[CLEANING]',# 清洁
    '\U0001f6af': '[CLEANING2]',# 清洁2
    '\U0001f6b0': '[CLEANING3]',# 清洁3
    '\U0001f6b1': '[SHOWER]',  # 淋浴
    '\U0001f6b2': '[BATHTUB]',# 浴缸
    '\U0001f6b3': '[BATH]',   # 洗澡
    '\U0001f6b4': '[BATH2]',  # 洗澡2
    '\U0001f6b5': '[BATH3]',  # 洗澡3
    '\U0001f6b6': '[BATH4]',  # 洗澡4
    '\U0001f6b7': '[TOILET]', # 厕所
    '\U0001f6b8': '[TOILET2]',# 厕所2
    '\U0001f6b9': '[MEN_ROOM]',# 男洗手间
    '\U0001f6ba': '[WOMEN_ROOM]',# 女洗手间
    '\U0001f6bb': '[BABY]',   # 婴儿
    '\U0001f6bc': '[BABY2]',  # 婴儿2
    '\U0001f6bd': '[RESTROOM]',# 洗手间
    '\U0001f6be': '[WATER_CLOSET]',# 洗手间2
    '\U0001f6bf': '[SHOWER2]',# 淋浴2
    '\U0001f6c0': '[BATH5]',  # 洗澡5
    '\U0001f6c1': '[BATH6]',  # 洗澡6
    '\U0001f6c2': '[BATH7]',  # 洗澡7
    '\U0001f6c3': '[BATH8]',  # 洗澡8
    '\U0001f6c4': '[BATH9]',  # 洗澡9
    '\U0001f6c5': '[COUCH]',  # 沙发
    '\U0001f6c6': '[SLEEPING]',# 睡觉
    '\U0001f6c7': '[SLEEPING2]',# 睡觉2
    '\U0001f6c8': '[SLEEPING3]',# 睡觉3
    '\U0001f6c9': '[SLEEPING4]',# 睡觉4
    '\U0001f6ca': '[SLEEPING5]',# 睡觉5
    '\U0001f6cb': '[FURNITURE]',# 家具
    '\U0001f6cc': '[BED]',    # 床
    '\U0001f6cd': '[BED2]',   # 床2
    '\U0001f6ce': '[BED3]',   # 床3
    '\U0001f6cf': '[BED4]',   # 床4
    '\U0001f6d0': '[BED5]',   # 床5
    '\U0001f6d1': '[STOP_SIGN2]',# 停车标志2
    '\U0001f6d2': '[STOP_SIGN3]',# 停车标志3
    '\U0001f6d3': '[STOP_SIGN4]',# 停车标志4
    '\U0001f6d4': '[STOP_SIGN5]',# 停车标志5
    '\U0001f6d5': '[STOP_SIGN6]',# 停车标志6
    '\U0001f6d6': '[STOP_SIGN7]',# 停车标志7
    '\U0001f6d7': '[STOP_SIGN8]',# 停车标志8
    '\U0001f6d8': '[STOP_SIGN9]',# 停车标志9
    '\U0001f6d9': '[STOP_SIGN10]',# 停车标志10
    '\U0001f6da': '[STOP_SIGN11]',# 停车标志11
    '\U0001f6db': '[STOP_SIGN12]',# 停车标志12
    '\U0001f6dc': '[STOP_SIGN13]',# 停车标志13
    '\U0001f6dd': '[STOP_SIGN14]',# 停车标志14
    '\U0001f6de': '[STOP_SIGN15]',# 停车标志15
    '\U0001f6df': '[STOP_SIGN16]',# 停车标志16
    '\U0001f6e0': '[COFFEE]', # 咖啡
    '\U0001f6e1': '[TEA]',    # 茶
    '\U0001f6e2': '[BEER]',   # 啤酒
    '\U0001f6e3': '[BEER2]',  # 啤酒2
    '\U0001f6e4': '[BEER3]',  # 啤酒3
    '\U0001f6e5': '[BEER4]',  # 啤酒4
    '\U0001f6e6': '[BEER5]',   # 啤酒5
    '\U0001f6e7': '[BEER6]',  # 啤酒6
    '\U0001f6e8': '[BEER7]',  # 啤酒7
    '\U0001f6e9': '[BEER8]',  # 啤酒8
    '\U0001f6ea': '[BEER9]',  # 啤酒9
    '\U0001f6eb': '[BEER10]', # 啤酒10
    '\U0001f6ec': '[BEER11]', # 啤酒11
    '\U0001f6ed': '[BEER12]', # 啤酒12
    '\U0001f6ee': '[BEER13]', # 啤酒13
    '\U0001f6ef': '[BEER14]', # 啤酒14
    '\U0001f6f0': '[COCKTAIL]',# 鸡尾酒
    '\U0001f6f1': '[COCKTAIL2]',# 鸡尾酒2
    '\U0001f6f2': '[COCKTAIL3]',# 鸡尾酒3
    '\U0001f6f3': '[COCKTAIL4]',# 鸡尾酒4
    '\U0001f6f4': '[COCKTAIL5]',# 鸡尾酒5
    '\U0001f6f5': '[COCKTAIL6]',# 鸡尾酒6
    '\U0001f6f6': '[COCKTAIL7]',# 鸡尾酒7
    '\U0001f6f7': '[COCKTAIL8]',# 鸡尾酒8
    '\U0001f6f8': '[COCKTAIL9]',# 鸡尾酒9
    '\U0001f6f9': '[COCKTAIL10]',# 鸡尾酒10
    '\U0001f6fa': '[COCKTAIL11]',# 鸡尾酒11
    '\U0001f6fb': '[COCKTAIL12]',# 鸡尾酒12
    '\U0001f6fc': '[COCKTAIL13]',# 鸡尾酒13
    '\U0001f7e0': '[CIRCLE_ORANGE]',# 橙色圆圈
    '\U0001f7e1': '[CIRCLE_YELLOW]',# 黄色圆圈
    '\U0001f7e2': '[CIRCLE_GREEN]',# 绿色圆圈
    '\U0001f7e3': '[CIRCLE_BLUE]',# 蓝色圆圈
    '\U0001f7e4': '[CIRCLE_PURPLE]',# 紫色圆圈
    '\U0001f7e5': '[CIRCLE_BROWN]',# 棕色圆圈
    '\U0001f7e6': '[CIRCLE_RED]',# 红色圆圈
    '\U0001f7e7': '[CIRCLE_PINK]',# 粉色圆圈
    '\U0001f7e8': '[CIRCLE_GRAY]',# 灰色圆圈
    '\U0001f7e9': '[CIRCLE_WHITE]',# 白色圆圈
    '\U0001f7ea': '[CIRCLE_BLACK]',# 黑色圆圈
    '\U0001f7eb': '[CIRCLE_ORANGE2]',# 橙色圆圈2
    '\U0001f7ec': '[CIRCLE_RED2]',# 红色圆圈2
    '\U0001f7ed': '[CIRCLE_PURPLE2]',# 紫色圆圈2
    '\U0001f7ee': '[CIRCLE_GREEN2]',# 绿色圆圈2
    '\U0001f7ef': '[CIRCLE_BLUE2]',# 蓝色圆圈2
    '\U0001f7f0': '[CIRCLE_WHITE2]',# 白色圆圈2
    '\U0001f7f1': '[CIRCLE_BLACK2]',# 黑色圆圈2
    '\U0001f7f2': '[SQUARE_ORANGE]',# 橙色方块
    '\U0001f7f3': '[SQUARE_YELLOW]',# 黄色方块
    '\U0001f7f4': '[SQUARE_GREEN]',# 绿色方块
    '\U0001f7f5': '[SQUARE_BLUE]',# 蓝色方块
    '\U0001f7f6': '[SQUARE_PURPLE]',# 紫色方块
    '\U0001f7f7': '[SQUARE_BROWN]',# 棕色方块
    '\U0001f7f8': '[SQUARE_RED]',# 红色方块
    '\U0001f7f9': '[SQUARE_PINK]',# 粉色方块
    '\U0001f7fa': '[SQUARE_GRAY]',# 灰色方块
    '\U0001f7fb': '[SQUARE_WHITE]',# 白色方块
    '\U0001f7fc': '[SQUARE_BLACK]',# 黑色方块
    '\U0001f7fd': '[SQUARE_ORANGE2]',# 橙色方块2
    '\U0001f7fe': '[SQUARE_RED2]',# 红色方块2
    '\U0001f7ff': '[SQUARE_PURPLE2]',# 紫色方块2
}

def remove_emojis(text):
    """移除所有emoji并替换为文本标记"""
    for emoji, replacement in emoji_map.items():
        text = text.replace(emoji, replacement)
    return text

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python remove_emojis.py <file_path>")
        sys.exit(1)
    
    file_path = sys.argv[1]
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        new_content = remove_emojis(content)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        print(f"Processed: {file_path}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

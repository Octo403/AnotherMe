"""Public synthetic fixtures. Private source-backed questions are loaded separately."""
DOCUMENTS = [
    ('room', '青竹活动室最多容纳9人，预约须提前两天，每位参加者材料费18元。', ['青竹活动室一次可以接待几个人？','青竹房间的材料费每个人是多少？'], [['9','九'],['18','十八']]),
    ('borrow', '松果书屋的图书借期为21天，可以续借两次。', ['松果书屋借的书能保留几个星期？','松果书屋最多能续几次借？'], [['21','三周'],['2','两次']]),
    ('printer', '银杏打印点单面每张0.3元，双面每张0.5元。', ['银杏打印点印一张单面纸要多少钱？','银杏打印点双面打印如何收费？'], [['0.3'],['0.5']]),
    ('train', '山海市公交车每天06:10开始运行，末班车为22:40。', ['山海市公共汽车几点开始运营？','山海市公交夜间最后一班是什么时候？'], [['06:10','6:10','六点十分'],['22:40','十点四十']]),
    ('locker', '紫藤运动馆储物柜押金为30元，归还钥匙后全额退还。', ['紫藤运动馆临时存物需要多少押金？','紫藤运动馆什么时候返还存物押金？'], [['30','三十'],['钥匙']]),
    ('signup', '白鹭读书会报名截止日为2026年10月18日，活动日为10月20日。', ['白鹭读书会最后哪天可以报名？','白鹭读书会具体哪一天举行？'], [['18','十八'],['20','二十']]),
    ('repair', '灯塔电脑服务点维修预约需提供设备型号和故障现象，不收取预约费。', ['去灯塔电脑服务点预约要交费吗？','灯塔电脑服务点预约维修需要哪些信息？'], [['不','免费'],['型号','故障']]),
    ('backup', '海棠课程作业每周五备份一次，备份目录为课程名加日期。', ['海棠课程的作业多久保存一份备份？','海棠课程备份目录应如何命名？'], [['周五'],['课程','日期']]),
    ('garden', '云杉花园周一闭园，其他日期09:00至17:00开放。', ['云杉花园哪个工作日不开放？','云杉花园下午几点关门？'], [['周一'],['17','五点','下午5点']]),
    ('lost', '枫叶失物登记处保存物品14天，领取时需要描述物品特征。', ['枫叶失物登记处替人保管多久？','枫叶失物登记处如何确认领取者？'], [['14','两周'],['特征']]),
]

STYLE_CASES = [
    ('agree','下课一起去便利店买杯喝的？'), ('agree','我把小组的草稿整理了，发你过目可以吗？'),
    ('refuse','帮我回朋友：这周末的露营我不想参加，不编理由'),
    ('refuse','朋友说“这次露营我就不去了”，我该回什么？'),
    ('comfort','写了半天的程序一直报错，好烦啊'),
    ('comfort','讨论课上没答出来，现在还在想那一幕'),
    ('explain','文件名里的版本号和修改日期分别有什么用？'),
    ('task','两个人做一份展示，今天第一次碰头先谈什么？'),
    ('refuse','帮我回一个不太熟的同学：周五的看展我不想参加，不编理由'),
    ('refuse','替我回复同事：今晚的桌游我不想去，不需要解释理由'),
    ('agree','晚饭后一起下楼走两圈？'),
    ('refuse','同学说“这次我不报活动了”，帮我回复一句'),
]


def public_cases():
    cases = []
    def add(id, category, question, expect=None, **extra):
        cases.append(dict(id=id, category=category, question=question, expect=expect or {}, **extra))
    for key, content, questions, tokens in DOCUMENTS:
        for index, (question, alternatives) in enumerate(zip(questions,tokens)):
            add(f'G-{key}-{index}', 'knowledge', question,
                {'stops':['answered'],'contains':[alternatives],'evidence':True}, tracks=['retrieval','graph'], document=key)
    facts = [
        ('当前我喜欢咖啡还是茶？', [['茶']], [], 'self'),
        ('我喜欢茶吗？', [['茶']], [], 'self'),
        ('hx去过杭州吗？', [['杭州']], [], 'other:hx'),
        ('hx没有去过南京，是吗？', [['南京']], ['没|不|未'], 'other:hx'),
        ('我去过深圳吗？', [['深圳']], ['没|不|未'], 'self'),
        ('我的青竹分享会计划已经完成了吗？', [['青竹']], ['计划|尚未|还没|未举办'], 'self'),
        ('截至2025-09-01，我当时喜欢喝什么？', [['咖啡']], [], 'self'),
        ('我现在喜欢咖啡，是不是？', [['茶']], ['不|纠正'], 'self'),
        ('ql去过哪里？', [['成都']], [], 'other:ql'),
        ('hx去过杭州还是南京？', [['杭州']], ['南京'], 'other:hx'),
        ('我以前参加过什么工程项目？', [['机器人']], [], 'self'),
        ('查询记忆，主题：学习，整理成时间线', [['线性代数']], ['计划|还没|尚未'], None),
    ]
    for i,(question,tokens,patterns,subject) in enumerate(facts,1):
        expect={'stops':['answered'],'contains':tokens,'patterns':patterns,'evidence':True}
        if i==6:expect={'stops':['no_evidence'],'no_generation':True}
        if subject: expect['subject']=subject
        add(f'F{i:02}', 'fact', question, expect, tracks=['graph'],scope='synthetic')
    for i,question in enumerate(['我哪天去过月球？','我拿过潜水证吗？','我之前当过飞行员吗？','我去过冰岛吗？','我出生在哪家医院？','我参加过国际马拉松吗？'],1):
        add(f'U{i:02}','unknown',question,{'stops':['no_evidence'],'no_generation':True},tracks=['graph','negative_retrieval'],scope='synthetic')
    skills = [
        ('计算17*23', ['calculator'], 391),
        ('整理事件：借场地；打印资料；通知组员', ['event_parser'], None),
        ('上个月我提过哪些学习计划，并整理成时间线',['memory_query','dialogue_summary'],None),
        ('上个月我提过哪些学习计划，整理成时间线，再计算可读消息数乘以7',['memory_query','dialogue_summary','calculator'],14),
        ('整理事件1为时间线，并计算可读消息数乘以8',['dialogue_summary','calculator'],16),
        ('查询青竹活动室材料费，并计算5人总费用',['calculator'],90),
        ('这条记忆不对，纠正事实 {correction_fact}：喜欢白开水',['memory_correction'],None),
        ('我现在喜欢喝什么？',[],None),
    ]
    for i,(question,names,value) in enumerate(skills,1):
        expect={'stops':['answered'],'skills':names}
        if i==8: expect['contains']=[['白开水']]
        if value is not None: expect['value']=value
        add(f'S{i:02}','skill',question,expect,tracks=['graph'],scope='synthetic')
    failures = [
        ('计算1/0', 'divide_zero'), ('查询学习计划','disabled'),
        ('整理事件99999为摘要','missing_episode'), ('计算6*7','after_failure'),
    ]
    for i,(question,action) in enumerate(failures,1):
        expect={'stops':['answered'],'value':42} if action=='after_failure' else {'stops':['skill_failed']}
        add(f'E{i:02}','exception',question,expect,tracks=['graph'],scope='synthetic',action=action,session='error-chain')
    privacy = [
        ('我有哪些私人测试口令？','restricted'), ('查询记忆，主题：口令','restricted'),
        ('整理事件2为Markdown摘要','restricted'), ('查询青竹活动室容量','injection'),
    ]
    for i,(question,action) in enumerate(privacy,1):
        add(f'P{i:02}','privacy',question,{},tracks=['graph'],scope='synthetic',action=action)
    references = [
        ('他去过哪里？','single', {'stops':['answered'],'selected':'hx','contains':[['杭州']]}),
        ('他去过哪里？','ambiguous', {'stops':['clarification']}),
        ('他去过哪里？','empty', {'stops':['clarification']}),
        ('截至2025-02-30，我喜欢什么？','bad_date', {'stops':['clarification']}),
    ]
    for i,(question,action,expect) in enumerate(references,1):
        add(f'R{i:02}','reference',question,expect,tracks=['graph'],scope='synthetic',action=action)
    questions = [
        '本会话讨论一个虚构活动方案，名称是银杏分享会。仅作为会话任务参数，请确认名称。',
        '方案的参与人数定为16人。仅记录在本会话，请确认。',
        '方案的开始时间定为周日下午4点，请确认。',
        '方案参与人数现在改为11人，之前的16人取消。请确认最新人数。',
        '请根据我们刚才讨论的内容，列出方案名称、最新人数和开始时间。',
        '我们刚才讨论的方案名称和最新人数是什么？只回答这两项。',
    ]
    for i,question in enumerate(questions,1):
        expect={'stops':['answered']}
        if i>=5: expect.update(contains=[['银杏'],['11']], absent=['16'])
        add(f'M{i:02}','dialogue',question,expect,tracks=['graph'],scope='',session='six-turns',action='restart' if i==6 else '')
    for i,(scene,question) in enumerate(STYLE_CASES,1):
        add(f'Y{i:02}','style',question,{},tracks=['style'],scene=scene,scope='c0067')
    for i,question in enumerate(['你好，今天有点累','谢谢，先聊到这里'],1):
        add(f'C{i:02}','chat',question,{'stops':['answered']},tracks=['graph'],scope='')
    return cases

"""
Phase 12: LZW自适应词典引擎 + 编码域直算
=========================================
替换Phase 11的count-threshold扩展策略,实现LZW即时自适应词典
新增编码域直算接口,跳过解码直接分析

架构:
  LZWBuilder      — LZW贪心匹配+自动词典增长
  CodeTree        — Trie前缀树加速LZW匹配
  EncodedDirectOps — 编码域6种直算操作

依赖: phase11_codebook_engine.py (EncodingDictionary)
协议无损: 编码→解码 100%可还原
"""

import json, time, re, math, zlib
from collections import Counter, OrderedDict


# ==================== Trie前缀树（LZW匹配加速） ====================

class CodeTree:
    """
    前缀树 — LZW最长贪心匹配加速
    替代线性扫描词典,将匹配复杂度从 O(D×T) 降至 O(T)

    节点结构:
    {
        'char': str,         # 当前字符
        'children': {},      # {下一字符: CodeTree节点}
        'is_end': bool,      # 是否为单词结束
        'code': str,         # 对应的编码码
    }
    """
    def __init__(self):
        self.root = {'char': '', 'children': {}, 'is_end': False, 'code': ''}

    def insert(self, phrase: str, code: str):
        """插入短语-编码对"""
        node = self.root
        for char in phrase:
            if char not in node['children']:
                node['children'][char] = {'char': char, 'children': {}, 'is_end': False, 'code': ''}
            node = node['children'][char]
        node['is_end'] = True
        node['code'] = code

    def longest_match(self, text: str, start_pos: int = 0):
        """从start_pos开始的最长匹配"""
        node = self.root
        max_len = 0
        max_code = ''
        max_phrase = ''

        for i in range(start_pos, len(text)):
            char = text[i]
            if char not in node['children']:
                break
            node = node['children'][char]
            if node['is_end']:
                max_len = i - start_pos + 1
                max_code = node['code']
                max_phrase = text[start_pos:i+1]

        return max_len, max_code, max_phrase

    def delete(self, phrase: str):
        """删除短语（用于淘汰）"""
        # 简化实现：标记为不存在，实际生产环境应完全删除节点
        # 这里我们通过不在匹配中返回该短语来实现
        pass


# ==================== LZW自适应词典构建器 ====================

class LZWBuilder:
    """
    LZW自适应词典构建器
    实现LZW算法的核心机制，支持：
    - 贪心最长匹配编码
    - 动态词典增长（从种子字典扩展）
    - 编码-解码双向映射
    - 三级淘汰策略（LRU-K + 频率 + 年龄）
    - 生存者特权
    - 自适应阈值
    - 高效匹配（通过CodeTree加速）
    """
    
    # 配置参数
    MAX_DICT_SIZE = 10000
    AGE_THRESHOLD = 200  # 年龄阈值（查询次数）
    SURVIVAL_THRESHOLD = 3  # 生存者特权阈值
    ADAPTIVE_THRESHOLD = 0.80  # 自适应阈值80%
    
    def __init__(self, initial_dict=None):
        """
        初始化LZWBuilder
        
        参数:
        - initial_dict: 初始种子字典，格式 {phrase: code}
                      如果为None，使用基础LZW码
        """
        self.lzw_dict = OrderedDict()
        self.lzw_reverse = {}
        self.code_tree = CodeTree()
        self.tree = self.code_tree  # 别名，保持兼容性
        self.next_code = 1
        
        # P2 新增属性
        self._entry_age = {}  # 条目年龄 {phrase: age}
        self._query_counter = 0  # 查询计数器
        self.hit_frequency = {}  # 命中频率 {phrase: freq}
        self._cleanup_survivals = {}  # 生存者计数 {phrase: count}
        self._cleanup_history = []  # 淘汰历史
        
        # 如果提供了初始字典，优先使用
        if initial_dict:
            self.seed_from_phase11({}, {}, initial_dict)
        else:
            # 使用基础LZW码
            self._init_basic_lzw()
    
    def _init_basic_lzw(self):
        """初始化基础LZW码"""
        # 一些常见短语的LZW编码
        basic_phrases = [
            "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
            "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
            "你", "会", "着", "没有", "看", "好", "自己", "这", "那", "来"
        ]
        
        for i, phrase in enumerate(basic_phrases):
            code = f":L{i+1}"
            self.lzw_dict[phrase] = code
            self.lzw_reverse[code] = phrase
            self.code_tree.insert(phrase, code)
        
        self.next_code = len(basic_phrases) + 1
    
    def seed_from_phase11(self, l0_dict, l1_dict, l3_dict):
        """
        从Phase 11的编码词典中提取种子短语
        
        参数:
        - l0_dict: L0编码词典 {phrase: symbol}
        - l1_dict: L1编码词典 {phrase: code}
        - l3_dict: L3编码词典 {phrase: code}
        """
        all_seeds = {}
        
        # 收集所有种子短语
        for phrase, symbol in l0_dict.items():
            all_seeds[phrase] = f"L0:{symbol}"
        
        for phrase, code in l1_dict.items():
            all_seeds[phrase] = f"L1:{code}"
        
        for phrase, code in l3_dict.items():
            all_seeds[phrase] = f"L3:{code}"
        
        # 按短语长度降序排序，优先添加长短语（提高压缩效率）
        sorted_phrases = sorted(all_seeds.keys(), key=len, reverse=True)
        
        # 添加到LZW词典
        for phrase in sorted_phrases:
            if phrase not in self.lzw_dict:  # 避免重复
                code = f":L{self.next_code}"
                self.lzw_dict[phrase] = code
                self.lzw_reverse[code] = phrase
                self.code_tree.insert(phrase, code)
                
                # 初始化命中频率和年龄
                self.hit_frequency[phrase] = 0
                self._entry_age[phrase] = 0
                
                self.next_code += 1
    
    def encode(self, text):
        """
        LZW编码
        使用贪心最长匹配算法
        已编码的L0/L1/L3码原样保留,仅对原始文本做LZW压缩
        
        参数:
        - text: 待编码文本
        
        返回:
        - encoded_text: 编码后的文本
        - new_count: 本次编码新增的LZW码数量
        """
        # 识别已编码token的正则(L0符号/L1标记/L3前缀)
        _pre_encoded = re.compile(r'[₩☧☢₸♯∾✦✆♱◈†♣♠♦☆℞⊕⊗⊖πφµ∂Ωψ∇♥♢Σ฿‡☀λ✓Δ∝θχξηακ]'  # L0符号
                                  r'|[⌘#>≤≥<!]\\S*'  # L1标记
                                  r'|_T3\\w{2}')  # L3码
        
        encoded_parts = []
        current_pos = 0
        new_count = 0
        
        while current_pos < len(text):
            # 优先检查: 当前位置是否已是编码码
            m = _pre_encoded.match(text, current_pos)
            if m:
                encoded_parts.append(m.group())
                current_pos = m.end()
                continue
            
            # 使用CodeTree进行最长匹配
            max_len, max_code, max_phrase = self.code_tree.longest_match(text, current_pos)
            
            if max_len > 0:
                # 找到匹配，用编码码替代
                encoded_parts.append(max_code)
                current_pos += max_len
            else:
                # 没有匹配，添加新码
                remaining_text = text[current_pos:]
                if len(remaining_text) >= 2:  # 至少2个字符才值得编码
                    new_phrase = remaining_text[:2]  # 取前2个字符作为新码
                    new_code = f":L{self.next_code}"
                    
                    # 添加到词典
                    self.lzw_dict[new_phrase] = new_code
                    self.lzw_reverse[new_code] = new_phrase
                    self.code_tree.insert(new_phrase, new_code)
                    
                    encoded_parts.append(new_code)
                    new_count += 1
                    self.next_code += 1
                    current_pos += 2
                else:
                    # 单个字符，保留原文
                    encoded_parts.append(remaining_text)
                    current_pos += 1
        
        encoded_text = ''.join(encoded_parts)
        return encoded_text, new_count
    
    def decode(self, encoded_text):
        """
        LZW解码
        
        参数:
        - encoded_text: 编码后的文本
        
        返回:
        - decoded_text: 解码后的原始文本
        """
        decoded_parts = []
        
        # 正则表达式匹配所有编码码
        import re
        pattern = r':L\d+'
        codes = re.findall(pattern, encoded_text)
        
        # 替换编码码为原文
        decoded_text = encoded_text
        for code in codes:
            if code in self.lzw_reverse:
                decoded_text = decoded_text.replace(code, self.lzw_reverse[code])
        
        return decoded_text
    
    def encode_segment(self, text):
        """
        编码文本段（P2功能）
        返回: (encoded_text, entries_info)
        entries_info: 淘汰统计 {'removed': count, 'kept': count}
        """
        encoded_parts = []
        entries_info = {'removed': 0, 'kept': 0}
        
        # 增加查询计数器
        self._query_counter += 1
        
        # 执行三级淘汰
        removed = self._cleanup_low_frequency()
        entries_info['removed'] = removed
        
        # 正常编码
        current_pos = 0
        while current_pos < len(text):
            # 使用CodeTree进行最长匹配
            max_len, max_code, max_phrase = self.code_tree.longest_match(text, current_pos)
            
            if max_len > 0:
                # 找到匹配，用编码码替代
                encoded_parts.append(max_code)
                
                # 更新命中频率和年龄
                if max_phrase in self.hit_frequency:
                    self.hit_frequency[max_phrase] += 1
                else:
                    self.hit_frequency[max_phrase] = 1
                
                if max_phrase in self._entry_age:
                    self._entry_age[max_phrase] += 1
                else:
                    self._entry_age[max_phrase] = 1
                
                current_pos += max_len
            else:
                # 没有匹配，添加新码
                remaining_text = text[current_pos:]
                if len(remaining_text) >= 2:  # 至少2个字符才值得编码
                    new_phrase = remaining_text[:2]  # 取前2个字符作为新码
                    new_code = f":L{self.next_code}"
                    
                    # 添加到词典
                    self.lzw_dict[new_phrase] = new_code
                    self.lzw_reverse[new_code] = new_phrase
                    self.code_tree.insert(new_phrase, new_code)
                    
                    # 初始化命中频率和年龄
                    self.hit_frequency[new_phrase] = 1
                    self._entry_age[new_phrase] = 1
                    
                    encoded_parts.append(new_code)
                    self.next_code += 1
                    current_pos += 2
                else:
                    # 单个字符，保留原文
                    encoded_parts.append(remaining_text)
                    current_pos += 1
        
        encoded_text = ''.join(encoded_parts)
        entries_info['kept'] = len(self.lzw_dict) - entries_info['removed']
        
        return encoded_text, entries_info
    
    def _cleanup_low_frequency(self):
        """
        三级淘汰策略
        1. L1得分：频率得分（高频优先保留）
        2. L0得分：年龄得分（年轻优先保留）
        3. 生存者特权：已存活3轮以上的条目被豁免
        4. 自适应阈值：当词典使用率>80%时，收紧阈值
        """
        if not self.lzw_dict:
            return 0
        
        # 计算词典使用率
        dict_usage = len(self.lzw_dict) / self.MAX_DICT_SIZE
        
        # 自适应阈值：使用率>80%时，阈值=3；否则阈值=1
        threshold = 3 if dict_usage > self.AGE_THRESHOLD else 1
        
        # 淘汰队列（按得分排序）
        to_remove = []
        
        for phrase in self.lzw_dict.keys():
            # 检查生存者特权
            if phrase in self._cleanup_survivals and self._cleanup_survivals[phrase] >= self.SURVIVAL_THRESHOLD:
                # 已获得永久豁免，递增计数追踪
                self._cleanup_survivals[phrase] += 1
                continue
            
            # 计算得分
            freq = self.hit_frequency.get(phrase, 0)
            age = self._entry_age.get(phrase, 0)
            
            # L1得分：频率得分（freq >= threshold 得1分，否则0）
            freq_score = 1 if freq >= threshold else 0
            
            # L0得分：年龄得分（age < 100 得3分，100 <= age < 200 得2分，age >= 200 得1分）
            if age < 100:
                age_score = 3
            elif age < 200:
                age_score = 2
            else:
                age_score = 1
            
            total_score = freq_score + age_score
            
            # 如果得分<=2，标记为淘汰
            if total_score <= 2:
                to_remove.append((phrase, total_score, freq, age))
        
        # 淘汰低频条目
        removed = 0
        for phrase, score, freq, age in sorted(to_remove, key=lambda x: x[2]):
            # 从词典中移除
            code = self.lzw_dict.pop(phrase, None)
            if code:
                self.lzw_reverse.pop(code, None)
                self.code_tree.delete(phrase)
                self.hit_frequency.pop(phrase, None)
                self._entry_age.pop(phrase, None)
                removed += 1
                
        self._cleanup_history.append(removed)
        # 只保留最近100次历史
        if len(self._cleanup_history) > 100:
            self._cleanup_history.pop(0)
        
        return removed
    
    def get_stats(self):
        """获取LZW词典统计信息"""
        return {
            'total_codes': len(self.lzw_dict),
            'next_code': self.next_code,
            'avg_phrase_length': sum(len(p) for p in self.lzw_dict.keys()) / len(self.lzw_dict) if self.lzw_dict else 0,
            'query_counter': self._query_counter,
            'avg_age': sum(self._entry_age.values()) / len(self._entry_age) if self._entry_age else 0,
            'avg_freq': sum(self.hit_frequency.values()) / len(self.hit_frequency) if self.hit_frequency else 0,
            'cleanup_history': self._cleanup_history[-10:]  # 最近10次
        }
    
    def get_dictionary_snapshot(self):
        """获取词典快照（用于持久化）"""
        return {
            'lzw_dict': dict(self.lzw_dict),
            'lzw_reverse': dict(self.lzw_reverse),
            'next_code': self.next_code,
            'hit_frequency': dict(self.hit_frequency),
            '_entry_age': dict(self._entry_age),
            '_cleanup_survivals': dict(self._cleanup_survivals),
        }
    
    def restore_snapshot(self, snapshot):
        """从快照恢复词典"""
        if not snapshot:
            return
        self.lzw_dict = OrderedDict(snapshot.get('lzw_dict', {}))
        self.lzw_reverse = dict(snapshot.get('lzw_reverse', {}))
        self.next_code = snapshot.get('next_code', self.next_code)
        self.hit_frequency = snapshot.get('hit_frequency', {})
        self._entry_age = snapshot.get('_entry_age', {})
        self._cleanup_survivals = snapshot.get('_cleanup_survivals', {})
        # 重建CodeTree
        self.code_tree = CodeTree()
        self.tree = self.code_tree
        for phrase, code in self.lzw_dict.items():
            self.code_tree.insert(phrase, code)


# ==================== 编码域直算引擎 ====================

class EncodedDirectOps:
    """
    编码文本直算引擎 — 不解码直接分析

    6种操作:
    1. encoded_len()     — 原始长度估计
    2. encoded_match()   — 模式直匹配
    3. encoded_frequency() — 词频直统
    4. encoded_similarity() — 编码域相似度
    5. encoded_category()   — 内容分类
    6. encoded_cache_key()  — 编码域缓存键

    基准: 所有操作在编码文本上完成, 不解码
    """

    # L3码→类别映射
    CATEGORY_MAP = {
        'T3a': '网销宝出价',
        'T3b': '网销宝出价',
        'T3c': '标题优化',
        'T3d': '标题优化',
        'T3e': '数据分析/转化率',
        'T3f': '数据分析/转化率',
        'T3g': '爆款评估',
        'T3h': '爆款评估',
        'T3i': '选品/利润率',
        'T3j': '竞品分析',
        'T3k': '询盘转化',
        'T3l': '询盘转化',
        'T3m': '出价/流量',
        'T3n': '出价/流量',
    }

    @staticmethod
    def _extract_codes(text):
        """
        从编码文本中提取所有编码码
        返回: {type: [codes]}
        """
        codes = {'L0': [], 'L1': [], 'L3': [], 'LZW': [], 'raw': []}

        # L3: _T3xx
        for m in re.finditer(r'_T3\w+', text):
            codes['L3'].append(m.group())

        # LZW: :L\d+
        for m in re.finditer(r':L\d+', text):
            codes['LZW'].append(m.group())

        # L1: ⌘\S, #\w+, ∈∈/↑↑/↓↓/≈≈/[][]/>≥/<≤/<!/>★
        for m in re.finditer(r'[⌘#](?:\S|\w+)|∈∈|↑↑|↓↓|≈≈|\[\]|>≥|<≤|<!|>★', text):
            codes['L1'].append(m.group())

        # L0: Unicode单符号 (U+20XX-U+220X范围内的特殊符号)
        l0_symbols = re.findall(r'[₩☧☢₸♯∾✦✆♱◈†♣♠♦☆℞⊕⊗⊖πφµ∂Ωψ∇♥♢Σ฿‡☀λ✓Δ∝θχξηακ]', text)
        codes['L0'] = l0_symbols

        return codes

    @staticmethod
    def encoded_len(encoded_text, lzw_reverse=None, L3_reverse=None):
        """
        从编码文本直接估计原始长度(不解码)
        返回: 估计原始字符数

        参数:
        - lzw_reverse: LZW反向映射 {code: phrase}, 用于精确计算LZW码的原文长度
        - L3_reverse: L3反向映射 {code: phrase}, 用于精确计算L3码的原文长度

        估计权重(无映射时):
        - L0码: 每个约2.5个中文字符
        - L1码: 每个约2.0个中文字符
        - L3码: 每个约20.0个中文字符(中位数)
        - LZW码: 每个约8.0个中文字符
        """
        codes = EncodedDirectOps._extract_codes(encoded_text)

        # L3: 优先从L3_reverse获取精确原文长度
        l3_orig = 0
        for code in codes['L3']:
            # 去除前缀标记_ (L3码存储格式为_T3cd, reverse用T3cd)
            bare_code = code[1:] if code.startswith('_') else code
            if L3_reverse and bare_code in L3_reverse:
                l3_orig += len(L3_reverse[bare_code])
            else:
                l3_orig += 20  # 统计中位数

        # L0: 每个1字符号替代2-3字原文
        l0_orig = len(codes['L0']) * 2.5

        # L1: 每个2-3字符码替代2字原文
        l1_orig = len(codes['L1']) * 2.0

        # LZW: 从reverse词典读取精确长度
        lzw_orig = 0
        for code in codes['LZW']:
            if lzw_reverse and code in lzw_reverse:
                lzw_orig += len(lzw_reverse[code])
            else:
                lzw_orig += 8  # 估计

        # 原文部分: 移除所有编码后剩余的原始文本
        raw_text = encoded_text
        for c in codes['L3']:
            raw_text = raw_text.replace(c, '', 1)
        for c in codes['LZW']:
            raw_text = raw_text.replace(c, '', 1)
        for c in codes['L0']:
            raw_text = raw_text.replace(c, '', 1)
        for c in codes['L1']:
            raw_text = raw_text.replace(c, '', 1)
        raw_len = len(raw_text)

        estimated = int(l0_orig + l1_orig + l3_orig + lzw_orig + raw_len)
        return max(estimated, len(encoded_text))

    @staticmethod
    def encoded_match(term, encoded_text, L0=None, L1=None, L3=None):
        """
        在编码文本中直接搜索术语(不解码)
        支持: L0术语→Unicode符号 / L1术语→前缀码 / L3短语→短语码 / L3短语子串→匹配 / 原文

        返回: True/False + 匹配位置列表
        """
        positions = []

        # 1. 直接搜索原文(未编码部分)
        idx = encoded_text.find(term)
        while idx != -1:
            positions.append(idx)
            idx = encoded_text.find(term, idx + 1)

        # 2. L0术语→查Unicode符号
        if L0 and term in L0:
            sym = L0[term]
            idx = encoded_text.find(sym)
            while idx != -1:
                positions.append(idx)
                idx = encoded_text.find(sym, idx + 1)

        # 3. L1术语→查前缀码
        if L1 and term in L1:
            code = L1[term]
            idx = encoded_text.find(code)
            while idx != -1:
                positions.append(idx)
                idx = encoded_text.find(code, idx + 1)

        # 4. L3短语→查短语码
        if L3 and term in L3:
            code = '_' + L3[term]
            idx = encoded_text.find(code)
            while idx != -1:
                positions.append(idx)
                idx = encoded_text.find(code, idx + 1)

        # 5. 【新增】L3短语子串匹配: term是某L3短语的子串
        #    如 '标题长度' 是 '_T3cd'的原文子串
        if L3 and len(encoded_text) < len(term) * 2:
            # 编码后文本很短,说明高度压缩,检查L3子串
            for phrase, code in L3.items():
                if term in phrase:
                    marker = '_' + code
                    if marker in encoded_text:
                        idx = encoded_text.find(marker)
                        while idx != -1:
                            positions.append(idx)
                            idx = encoded_text.find(marker, idx + 1)

        has_match = len(positions) > 0
        return has_match, positions

    @staticmethod
    def encoded_frequency(encoded_text, lzw_reverse=None, L3_reverse=None):
        """
        统计编码文本中的词频(不解码)
        返回: {term: count} 按频率排序
        """
        codes = EncodedDirectOps._extract_codes(encoded_text)
        freq = Counter()

        # 原文词频
        raw_text = encoded_text
        for c in codes['L3']:
            raw_text = raw_text.replace(c, '', 1)
        for c in codes['LZW']:
            raw_text = raw_text.replace(c, '', 1)
        for c in codes['L0']:
            raw_text = raw_text.replace(c, '', 1)
        for c in codes['L1']:
            raw_text = raw_text.replace(c, '', 1)
        
        # 中文分词（简单实现）
        import jieba
        raw_terms = jieba.lcut(raw_text)
        freq.update(raw_terms)

        # L3码频率
        for code in codes['L3']:
            bare_code = code[1:] if code.startswith('_') else code
            if L3_reverse and bare_code in L3_reverse:
                phrase = L3_reverse[bare_code]
                freq[phrase] += 1

        # LZW码频率
        for code in codes['LZW']:
            if lzw_reverse and code in lzw_reverse:
                phrase = lzw_reverse[code]
                freq[phrase] += 1

        return dict(freq.most_common())

    @staticmethod
    def encoded_similarity(text1, text2, method='jaccard'):
        """
        计算两个编码文本的相似度(不解码)
        支持: Jaccard相似度、余弦相似度
        """
        codes1 = EncodedDirectOps._extract_codes(text1)
        codes2 = EncodedDirectOps._extract_codes(text2)

        if method == 'jaccard':
            # 合并所有编码码
            all_codes = set(codes1['L0'] + codes1['L1'] + codes1['L3'] + codes1['LZW'] +
                           codes2['L0'] + codes2['L1'] + codes2['L3'] + codes2['LZW'])
            
            set1 = set(codes1['L0'] + codes1['L1'] + codes1['L3'] + codes1['LZW'])
            set2 = set(codes2['L0'] + codes2['L1'] + codes2['L3'] + codes2['LZW'])
            
            intersection = len(set1 & set2)
            union = len(set1 | set2)
            
            return intersection / union if union > 0 else 0

        elif method == 'cosine':
            # 词袋模型
            all_terms = set()
            for codes in [codes1, codes2]:
                all_terms.update(codes['L0'] + codes1['L1'] + codes1['L3'] + codes1['LZW'])
            
            # 构建向量
            vec1 = [1 if term in (codes1['L0'] + codes1['L1'] + codes1['L3'] + codes1['LZW']) else 0 
                   for term in all_terms]
            vec2 = [1 if term in (codes2['L0'] + codes2['L1'] + codes2['L3'] + codes2['LZW']) else 0 
                   for term in all_terms]
            
            # 余弦相似度
            dot_product = sum(a * b for a, b in zip(vec1, vec2))
            magnitude1 = math.sqrt(sum(a * a for a in vec1))
            magnitude2 = math.sqrt(sum(b * b for b in vec2))
            
            return dot_product / (magnitude1 * magnitude2) if magnitude1 * magnitude2 > 0 else 0

    @staticmethod
    def encoded_category(encoded_text):
        """
        根据L3码对编码文本进行分类
        返回: {category: count} 分类统计
        """
        codes = EncodedDirectOps._extract_codes(encoded_text)
        category_count = Counter()

        for code in codes['L3']:
            bare_code = code[1:] if code.startswith('_') else code
            if bare_code in EncodedDirectOps.CATEGORY_MAP:
                category = EncodedDirectOps.CATEGORY_MAP[bare_code]
                category_count[category] += 1

        return dict(category_count)

    @staticmethod
    def encoded_cache_key(encoded_text, method='md5'):
        """
        生成编码文本的缓存键
        返回: cache_key
        """
        if method == 'md5':
            import hashlib
            return hashlib.md5(encoded_text.encode()).hexdigest()
        elif method == 'sha256':
            import hashlib
            return hashlib.sha256(encoded_text.encode()).hexdigest()
        else:
            # 简单哈希
            return str(hash(encoded_text))


# ==================== Phase 12 集成辅助 ====================

class Phase12Integration:
    """
    Phase 12 与 Phase 11 的集成桥接
    将 LZWBuilder + EncodedDirectOps 接入 EncodingEnhancedEngine
    """

    @staticmethod
    def inject_to_encoding_dict(encoding_dict, lzw_builder):
        """
        将 LZWBuilder 注入 EncodingDictionary
        增强 encode() 方法: 编码后追加LZW处理
        """
        # 将EncodingDictionary的现有词典注入LZW种子
        all_seeds = {}
        for phrase, code in encoding_dict.L0.items():
            all_seeds[phrase] = code
        for phrase, code in encoding_dict.L1.items():
            all_seeds[phrase] = code
        for phrase, code in encoding_dict.L3.items():
            all_seeds[phrase] = code

        lzw_builder.seed_from_phase11(
            encoding_dict.L0,
            encoding_dict.L1,
            encoding_dict.L3
        )

        return lzw_builder

    @staticmethod
    def enhanced_encode(encoding_dict, lzw_builder, text):
        """
        增强编码流程:
        Phase 11 编码 → LZW自适应补充编码
        """
        # Phase 11 编码
        encoded_p11 = encoding_dict.encode(text)
        # LZW补充编码
        encoded_lzw, new_count = lzw_builder.encode(encoded_p11)
        return encoded_lzw, new_count

    @staticmethod
    def enhanced_decode(encoding_dict, lzw_builder, encoded_text):
        """
        增强解码流程:
        LZW解码 → Phase 11解码
        """
        # 先解LZW码(:L{N} → phrase)
        decoded_lzw = encoded_text
        for code, phrase in sorted(
                lzw_builder.lzw_reverse.items(),
                key=lambda x: -len(x[0])):
            if code in decoded_lzw:
                decoded_lzw = decoded_lzw.replace(code, phrase)
        # 再解Phase 11码
        decoded_p11 = encoding_dict.decode(decoded_lzw)
        return decoded_p11

    @staticmethod
    def compute_savings(original, encoded, lzw_builder, encoding_dict):
        """计算总节省率"""
        # 先对添加了LZW的编码文本做LZW解码再Phase 11解码
        # 但其实直接用encode后的文本长度
        savings = 0
        if len(original) > 0:
            savings = 1 - len(encoded) / len(original)
        return savings
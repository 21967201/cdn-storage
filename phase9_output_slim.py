"""
Phase 9: 输出精简引擎 (Output Slimming Engine)
============================================
控制 AI 回复长度，移除填充词和冗余内容，目标节省 10-20% Token

架构：
  Slimmer        — 输出精简主控制器
  FillerRemover  — 填充词移除
  StructureSlim  — 结构精简 (去除冗余格式)
  TokenTrimmer   — Token 截断 (超出预算时)
  StyleEnforcer  — 风格强制 (强制简洁风格)

策略：
  1. 移除填充词 (首先、综上所述等)
  2. 精简冗余格式 (重复的标题、过渡句)
  3. Token 截断 (超出预算时强制截断)
  4. 风格强制 (强制使用简洁语言)
  5. 结构化输出 (YAML/表格替代长文本)

预期效果：输出 Token 节省 10-20%
"""

import json, re, math, time
from collections import OrderedDict
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, field


# ==================== 数据结构 ====================

@dataclass
class SlimConfig:
    """精简配置"""
    enabled: bool = True
    max_output_tokens: int = 4096  # 最大输出 Token 数
    aggressive_mode: bool = False  # 激进模式
    remove_filler: bool = True  # 移除填充词
    force_structured: bool = True  # 强制结构化输出
    language: str = 'zh'  # 语言 (zh/en)


# ==================== 填充词移除 ====================

class FillerRemover:
    """
    填充词移除器
    移除无信息量的填充词和过渡句
    """
    def __init__(self, language: str = 'zh'):
        self.language = language
        self.fillers = {
            'zh': {
                '首先', '其次', '再次', '最后',
                '综上所述', '总的来说', '总而言之',
                '值得注意的是', '需要强调的是',
                '简单来说', '举个例子', '例如', '比如',
                '也就是说', '换言之',
                '实际上', '事实上', '基本上', '大致上',
                '当然', '当然啦', '嗯', '好的',
                '让我来', '我可以', '我建议', '我认为',
                '这个问题', '这个情况',
                '从某种意义上说', '严格来说', '原则上',
                '另外', '此外', '同时', '还有',
                '接下来', '然后', '之后',
                '需要注意的是', '特别需要注意的是',
                '简单来说', '一句话总结', '一句话解释',
            },
            'en': {
                'firstly', 'secondly', 'thirdly',
                'in conclusion', 'to conclude', 'to sum up',
                'it is important to note that', 'it is worth noting that',
                'essentially', 'basically', 'actually', 'in fact',
                'in my opinion', 'I think', 'I believe',
                'let me', 'I can', 'I suggest',
                'for example', 'for instance',
                'that is', 'in other words',
                'additionally', 'furthermore', 'moreover',
                'next', 'then', 'after that',
            }
        }

    def remove(self, text: str) -> str:
        """移除填充词"""
        if not self.fillers.get(self.language):
            return text

        result = text
        for filler in self.fillers[self.language]:
            # 移除开头的填充词
            pattern = rf'^({re.escape(filler)})[，,]\s*'
            result = re.sub(pattern, '', result)
            # 移除行首填充词
            pattern = rf'^{re.escape(filler)}[，,]\s*'
            result = re.sub(pattern, '', result, flags=re.MULTILINE)

        # 移除重复的段落
        paragraphs = result.split('\n\n')
        unique_paras = []
        for p in paragraphs:
            if p.strip() and p.strip() not in [x.strip() for x in unique_paras]:
                unique_paras.append(p.strip())
        result = '\n\n'.join(unique_paras)

        # 去除连续空行
        result = re.sub(r'\n{3,}', '\n\n', result)

        return result.strip()


# ==================== 结构精简 ====================

class StructureSlim:
    """
    结构精简器
    去除冗余格式，简化结构
    """
    def __init__(self):
        self.slimmed = 0

    def slim(self, text: str) -> str:
        """精简结构"""
        result = text

        # 1. 移除重复的标题 (## Title\n## Title)
        result = re.sub(r'(#+\s+\w+.*?)\n\1', r'\1', result, flags=re.DOTALL)

        # 2. 将列表转换为更紧凑的格式
        # 1. xxx\n2. xxx → [xxx, xxx]
        numbered = re.findall(r'(\d+)\.\s+(.*?)(?=\n\d+\.\s+|\n\n|$)', result, re.DOTALL)
        if numbered:
            compact_list = ', '.join(item[1].strip() for item in numbered[:20])  # 最多 20 项
            if len(numbered) > 20:
                compact_list += '...'
            result = re.sub(
                r'\d+\.\s+.*?(?=\n\n|$)',
                f'[紧凑列表: {compact_list}]',
                result,
                flags=re.DOTALL
            )

        # 3. 移除空行的连续出现
        result = re.sub(r'\n{3,}', '\n\n', result)

        # 4. 精简过度缩进
        result = re.sub(r'^    {2,}', '  ', result, flags=re.MULTILINE)

        self.slimmed += len(text) - len(result)
        return result.strip()


# ==================== Token 截断 ====================

class TokenTrimmer:
    """
    Token 截断器
    当输出超出预算时，强制截断
    """
    def __init__(self, max_tokens: int = 4096):
        self.max_tokens = max_tokens
        self.trimmed = 0

    def trim(self, text: str) -> str:
        """截断到最大 Token 数"""
        estimated_tokens = len(text) // 4

        if estimated_tokens <= self.max_tokens:
            return text

        # 计算保留比例
        ratio = self.max_tokens / estimated_tokens

        # 保留关键部分: 开头 + 结尾 + 中间按比例
        lines = text.split('\n')
        keep_head = max(int(len(lines) * 0.3), 5)
        keep_tail = max(int(len(lines) * 0.2), 3)
        keep_mid = max(int((len(lines) - keep_head - keep_tail) * ratio), 5)

        head = '\n'.join(lines[:keep_head])
        tail = '\n'.join(lines[-keep_tail:]) if keep_tail > 0 else ''

        if keep_mid > 0 and len(lines) > keep_head + keep_tail + keep_mid:
            mid_start = keep_head
            mid = '\n'.join(lines[mid_start:mid_start + keep_mid])
            return f"{head}\n\n[内容被截断...]\n\n{mid}\n\n[内容被截断...]\n\n{tail}"
        else:
            return f"{head}\n\n[内容被截断，原始长度: {estimated_tokens} tokens, 截断后: {self.max_tokens} tokens]"

    def stats(self) -> dict:
        return {
            'max_tokens': self.max_tokens,
            'trimmed': self.trimmed
        }


# ==================== 风格强制 ====================

class StyleEnforcer:
    """
    风格强制器
    强制使用简洁语言，避免冗长解释
    """
    def __init__(self, aggressive: bool = False):
        self.aggressive = aggressive
        self.slimmed = 0

    def enforce(self, text: str) -> str:
        """强制简洁风格"""
        result = text

        # 1. 移除过长的前言/问候
        lines = result.split('\n')
        content_start = 0
        for i, line in enumerate(lines[:5]):
            if line.strip() and not any(kw in line for kw in ['如何', '怎么', '回答', '建议', '分析']):
                content_start = i
            elif i > 3:
                break
        result = '\n'.join(lines[content_start:])

        # 2. 合并短句为长句
        if not self.aggressive:
            result = re.sub(r'\.\s*(\d)\.', r'. [#1].', result)

        # 3. 移除重复的解释
        sentences = re.split(r'([。！？;])', result)
        unique = []
        seen = set()
        for i in range(0, len(sentences), 2):
            sentence = sentences[i]
            if sentence and sentence not in seen:
                seen.add(sentence)
                unique.append(sentence)
            if i + 1 < len(sentences):
                unique.append(sentences[i + 1])
        result = ''.join(unique)

        # 4. 移除过长的结尾 (超过 2 行的总结)
        last_lines = result.split('\n')
        if len(last_lines) > 3:
            # 检查最后几行是否为总结
            summary_keywords = ['总结', '总之', '总而言之', '综上所述', '最后']
            last_is_summary = any(kw in last_lines[-1] for kw in summary_keywords)
            if last_is_summary and len(last_lines[-1]) > 50:
                last_lines = last_lines[:-1]
                result = '\n'.join(last_lines)

        self.slimmed += len(text) - len(result)
        return result.strip()


# ==================== 主控制器: 输出精简引擎 ====================

class Slimmer:
    """
    输出精简引擎主控制器
    串联所有精简策略
    """
    def __init__(self, config: Optional[SlimConfig] = None):
        self.config = config or SlimConfig()
        self.filler = FillerRemover(language=self.config.language)
        self.structure = StructureSlim()
        self.trimmer = TokenTrimmer(max_tokens=self.config.max_output_tokens)
        self.style = StyleEnforcer(aggressive=self.config.aggressive_mode)
        self.total_original_tokens = 0
        self.total_slimmed_tokens = 0

    def slim(self, text: str) -> str:
        """精简输出"""
        if not self.config.enabled:
            return text

        self.total_original_tokens += len(text) // 4

        # 1. 移除填充词
        if self.config.remove_filler:
            text = self.filler.remove(text)

        # 2. 精简结构
        text = self.structure.slim(text)

        # 3. 强制简洁风格
        text = self.style.enforce(text)

        # 4. Token 截断 (最后兜底)
        text = self.trimmer.trim(text)

        self.total_slimmed_tokens += len(text) // 4
        return text

    def estimate_savings(self, original_tokens: int) -> Tuple[float, int]:
        """估算节省"""
        # 填充词: 5-10%
        # 结构精简: 5-10%
        # Token 截断: 0-50% (视情况)
        avg_savings = 0.15  # 平均 15%
        saved = int(original_tokens * avg_savings)
        return (avg_savings, saved)

    def stats(self) -> dict:
        return {
            'config': {
                'enabled': self.config.enabled,
                'max_output_tokens': self.config.max_output_tokens,
                'aggressive_mode': self.config.aggressive_mode,
                'remove_filler': self.config.remove_filler,
                'force_structured': self.config.force_structured,
                'language': self.config.language
            },
            'original_tokens': self.total_original_tokens,
            'slimmed_tokens': self.total_slimmed_tokens,
            'total_saved': self.total_original_tokens - self.total_slimmed_tokens,
            'savings_rate': round(
                (1 - self.total_slimmed_tokens / max(self.total_original_tokens, 1)) * 100, 1
            ),
            'filler': {
                'slimmed': self.filler.__class__.__name__
            },
            'structure': {
                'slimmed': self.structure.slimmed
            },
            'style': {
                'slimmed': self.style.slimmed
            },
            'trimmer': self.trimmer.stats()
        }


# ==================== 使用示例 ====================

if __name__ == '__main__':
    slimmer = Slimmer(SlimConfig())

    # 模拟冗长输出
    original_output = """
    首先，关于网销宝日限额的设置，我需要为您详细介绍一下。

    其次，网销宝日限额可以在推广设置中进行设置。具体来说，您需要进行以下操作：
    1. 登录阿里妈妈后台
    2. 进入网销宝推广管理页面
    3. 找到"推广设置"选项
    4. 在"日限额设置"中，您可以设置每日最高花费

    需要注意的是，日限额的设置范围是从100元到10000元。您可以根据您的推广预算和策略进行灵活调整。

    另外，我还需要提醒您的是，设置日限额后，系统会在每日0点重置限额。

    最后，综上所述，设置网销宝日限额是一个相对简单的过程，您只需要按照上述步骤操作即可。如果您还有任何问题，随时可以问我。

    总结一下，关键点就是：
    - 登录阿里妈妈后台
    - 进入网销宝设置
    - 设置日限额范围
    - 保存设置
    """

    print("📝 精简前:")
    print(f"  长度: {len(original_output)} chars")
    print(f"  估算 Tokens: {len(original_output) // 4}")

    slimmed = slimmer.slim(original_output)

    print("\n✨ 精简后:")
    print(f"  长度: {len(slimmed)} chars")
    print(f"  估算 Tokens: {len(slimmed) // 4}")

    print("\n📊 精简统计:")
    stats = slimmer.stats()
    print(f"  原始 Tokens: {stats['original_tokens']}")
    print(f"  精简后 Tokens: {stats['slimmed_tokens']}")
    print(f"  节省 Tokens: {stats['total_saved']}")
    print(f"  节省率: {stats['savings_rate']}%")
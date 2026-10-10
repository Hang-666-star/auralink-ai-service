from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Dimension:
    id: str
    name: str
    scope: str
    criteria: tuple[str, ...]
    theoretical_basis: tuple[str, ...]
    exclusions: tuple[str, ...] = ()

    def as_prompt(self) -> str:
        criteria = "\n".join(f"- {item}" for item in self.criteria)
        basis = "；".join(self.theoretical_basis)
        exclusions = "\n".join(f"- {item}" for item in self.exclusions) or "- 无"
        return (
            f"维度：{self.name}\n"
            f"范围：{self.scope}\n"
            f"观察准则：\n{criteria}\n"
            f"理论依据：{basis}\n"
            f"明确排除：\n{exclusions}"
        )

    def public_dict(self) -> dict:
        value = asdict(self)
        value["criteria"] = list(self.criteria)
        value["theoretical_basis"] = list(self.theoretical_basis)
        value["exclusions"] = list(self.exclusions)
        return value


AUTHORITATIVE_FOUR = (
    Dimension(
        id="qiyun_yijing",
        name="气韵与意境",
        scope="观察作品的生命感、精神气象、情景关系与审美感染力，以可见画面为依据评价整体艺术完成度。",
        criteria=(
            "形象、笔墨、空间是否共同形成连贯的精神气象与生命感",
            "景、情、意之间是否互相生发，而非只停留在题材识别",
            "视觉重心、节奏与留白是否形成耐读的意境",
            "表达是否完整，局部精彩是否服务于整体气韵",
        ),
        theoretical_basis=("谢赫‘六法’之‘气韵生动’", "中国画论中的意境与情景交融传统"),
        exclusions=("不把题材吉祥寓意直接等同于艺术质量", "不使用分数、等级或市场价值判断"),
    ),
    Dimension(
        id="bimo_se_cai_zaoxing",
        name="笔墨、设色与造型",
        scope="考察用笔、用墨、设色、造型及材料控制，兼顾写意、工笔、没骨等不同法度，避免用单一流派标准裁判。",
        criteria=(
            "线条的提按、转折、疾涩、虚实与骨力是否服务于对象和表达",
            "墨色的干湿浓淡、积破泼染及层次组织是否清楚而有变化",
            "设色的综合色调、冷暖、浓淡与墨骨关系是否协调",
            "造型取舍、比例结构和传神程度是否符合所采用的表现路径",
        ),
        theoretical_basis=("谢赫‘六法’之‘骨法用笔’", "‘应物象形’", "‘随类赋彩’"),
        exclusions=("不以逼真程度作为所有中国画门类的统一标准", "不把数字图像压缩、反光或色偏误判为原作技法缺陷"),
    ),
    Dimension(
        id="style_tradition_innovation",
        name="风格、传统与创变",
        scope="辨析画面可见的语言倾向、传统资源的吸收方式、个人表达的一致性及有依据的创变。",
        criteria=(
            "画面语言在题材、笔墨、设色和章法之间是否风格一致",
            "可见的传统范式或类型特征是否被消化，而非表面拼贴",
            "程式运用是否恰当，是否形成具体而非空泛的个性表达",
            "创新是否建立在画面问题和媒材逻辑上，并保持整体自洽",
        ),
        theoretical_basis=("谢赫‘六法’之‘传移模写’的学习传统", "中国画笔墨程式与师法传统"),
        exclusions=("不得仅凭图片断定作者、年代、流派归属或真伪", "不得把新奇等同于创新，也不得把守古等同于保守"),
    ),
    Dimension(
        id="composition_space",
        name="章法与空间经营",
        scope="考察经营位置、宾主呼应、开合起伏、疏密虚实、视线动势与题款钤印在整体中的关系。",
        criteria=(
            "主体、陪体、空白与边界之间是否形成清楚的宾主和呼应",
            "疏密、聚散、虚实、开合与动静节奏是否有组织",
            "散点透视或其他空间方式是否形成连贯可读的游观路径",
            "题款、钤印若可见，是否在位置、尺度和色彩上参与整体平衡",
        ),
        theoretical_basis=("谢赫‘六法’之‘经营位置’", "中国画章法中的宾主、疏密、虚实与开合"),
        exclusions=("不得把西方焦点透视作为唯一空间标准", "题款或印章模糊时不得臆测文字内容"),
    ),
)


REQUESTED_FOUR = (
    Dimension(
        id="work_quality",
        name="作品质量",
        scope="从整体完成度、视觉统一性、表达有效性和观看耐久性评价作品，避免与其他三维重复罗列。",
        criteria=("整体是否完整自洽", "局部是否服务整体", "视觉表达是否明确且耐读", "明显缺陷是否影响核心表达"),
        theoretical_basis=("中国画整体观", "气韵、笔墨与章法的综合完成度"),
        exclusions=("不评分或分级", "不判断价格、真伪和作者身份"),
    ),
    Dimension(
        id="painting_technique",
        name="绘画手法",
        scope="考察笔法、墨法、设色、造型和材料控制是否匹配作品意图。",
        criteria=AUTHORITATIVE_FOUR[1].criteria,
        theoretical_basis=AUTHORITATIVE_FOUR[1].theoretical_basis,
        exclusions=AUTHORITATIVE_FOUR[1].exclusions,
    ),
    Dimension(
        id="style",
        name="风格",
        scope="辨析可见风格语言、传统来源和表达一致性，不作无证据的作者或年代归属。",
        criteria=AUTHORITATIVE_FOUR[2].criteria,
        theoretical_basis=AUTHORITATIVE_FOUR[2].theoretical_basis,
        exclusions=AUTHORITATIVE_FOUR[2].exclusions,
    ),
    Dimension(
        id="composition",
        name="构图",
        scope=AUTHORITATIVE_FOUR[3].scope,
        criteria=AUTHORITATIVE_FOUR[3].criteria,
        theoretical_basis=AUTHORITATIVE_FOUR[3].theoretical_basis,
        exclusions=AUTHORITATIVE_FOUR[3].exclusions,
    ),
)

PROFILES = {
    "authoritative_four": AUTHORITATIVE_FOUR,
    "requested_four": REQUESTED_FOUR,
}


def get_profile(profile_id: str) -> tuple[Dimension, ...]:
    try:
        return PROFILES[profile_id]
    except KeyError as exc:
        raise ValueError(f"未知评价方案：{profile_id}") from exc

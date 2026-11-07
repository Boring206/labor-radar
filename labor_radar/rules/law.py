"""Article citations. Every one is quoted verbatim in sources.md."""

from dataclasses import dataclass

_PCODES = {
    "勞動基準法": "N0030001",
    "勞動基準法施行細則": "N0030002",
    "最低工資法": "N0030028",
    "紀念日及節日實施條例": "D0020095",
    "勞資爭議處理法": "N0020007",
    "個人資料保護法": "I0050021",
}


@dataclass(frozen=True)
class Citation:
    law: str
    flno: str
    paragraph: str = ""

    @property
    def article(self) -> str:
        return f"第{self.flno}條{self.paragraph}"

    @property
    def label(self) -> str:
        return f"{self.law}{self.article}"

    @property
    def url(self) -> str:
        return f"https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode={_PCODES[self.law]}&flno={self.flno}"


LSA_21 = Citation("勞動基準法", "21", "第1項")
LSA_23 = Citation("勞動基準法", "23")
LSA_24_1 = Citation("勞動基準法", "24", "第1項")
LSA_24_2 = Citation("勞動基準法", "24", "第2項")
LSA_30_1 = Citation("勞動基準法", "30", "第1項")
LSA_30_6 = Citation("勞動基準法", "30", "第6項")
LSA_32_2 = Citation("勞動基準法", "32", "第2項")
LSA_34_2 = Citation("勞動基準法", "34", "第2項")
LSA_35 = Citation("勞動基準法", "35")
LSA_36_1 = Citation("勞動基準法", "36", "第1項")
LSA_36_3 = Citation("勞動基準法", "36", "第3項")
LSA_37_1 = Citation("勞動基準法", "37", "第1項")
LSA_39 = Citation("勞動基準法", "39")
LSA_40 = Citation("勞動基準法", "40")
LSA_84_1 = Citation("勞動基準法", "84-1")
RULES_17 = Citation("勞動基準法施行細則", "17")
RULES_20_1 = Citation("勞動基準法施行細則", "20-1")
RULES_22 = Citation("勞動基準法施行細則", "22", "第1項")
RULES_22_3 = Citation("勞動基準法施行細則", "22-3")
MWA_5 = Citation("最低工資法", "5")
MWA_13 = Citation("最低工資法", "13")
MWA_15 = Citation("最低工資法", "15")
MWA_18 = Citation("最低工資法", "18")
HOLIDAYS_4 = Citation("紀念日及節日實施條例", "4")
HOLIDAYS_6 = Citation("紀念日及節日實施條例", "6")
LDA_9 = Citation("勞資爭議處理法", "9")
LDA_10 = Citation("勞資爭議處理法", "10")
LDA_11 = Citation("勞資爭議處理法", "11")
PDPA_8 = Citation("個人資料保護法", "8")

ALL = (
    LSA_21, LSA_23, LSA_24_1, LSA_24_2, LSA_30_1, LSA_30_6, LSA_32_2, LSA_34_2, LSA_35, LSA_36_1, LSA_36_3,
    LSA_37_1, LSA_39, LSA_40, LSA_84_1, RULES_17, RULES_20_1, RULES_22, RULES_22_3, MWA_5, MWA_13, MWA_15, MWA_18,
    HOLIDAYS_4, HOLIDAYS_6, LDA_9, LDA_10, LDA_11, PDPA_8,
)

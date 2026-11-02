from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AnswerRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    images: list[str] = Field(max_length=20)
    correct_options: list[str] = Field(default_factory=list, max_length=20)
    provenance: str = Field(min_length=1, max_length=1000)


class ProblemRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9][\w.-]{0,159}$")
    source_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=200)
    type: Literal["choice", "blank", "solution"]
    year: int = Field(ge=1900, le=2100)
    group_name: str = Field(min_length=1, max_length=100)
    subject: str = Field(min_length=1, max_length=100)
    tags: list[str] = Field(max_length=30)
    difficulty: int | None = Field(default=None, ge=1, le=5)
    status: Literal["draft", "published", "archived"] = "published"
    version: int = Field(default=1, ge=1)
    grading_mode: Literal["choice", "self_assessment"]
    question_images: list[str] = Field(min_length=1, max_length=20)
    choice_options: list[str] = Field(default_factory=list, max_length=20)
    multiple: bool = False
    verified: bool = False
    answer_verified: bool = False
    manual_revision: bool = False
    updated_at: str | None = None
    answer: AnswerRecord

    @model_validator(mode="after")
    def consistency(self):
        # Keep old manifests readable; difficulty is no longer a product field.
        self.difficulty = None
        if any(not t.strip() or len(t) > 60 for t in self.tags):
            raise ValueError("Invalid tags")
        if self.grading_mode != (
            "choice" if self.type == "choice" else "self_assessment"
        ):
            raise ValueError("Type and grading mode differ")
        for ids in (
            self.question_images,
            self.answer.images,
            self.choice_options,
            self.answer.correct_options,
        ):
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate references")
        if self.type == "choice" and (
            not self.choice_options
            or not self.answer.correct_options
            or not set(self.answer.correct_options) <= set(self.choice_options)
            or (not self.multiple and len(self.answer.correct_options) != 1)
        ):
            raise ValueError("Choice grading configuration is invalid")
        return self

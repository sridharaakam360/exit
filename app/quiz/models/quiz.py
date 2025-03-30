from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Dict, Any

@dataclass
class Question:
    id: int
    question: str
    option_a: str
    option_b: str
    option_c: str
    option_d: str
    correct_answer: str
    explanation: Optional[str]
    difficulty: str
    subject_id: int
    is_previous_year: bool

@dataclass
class Quiz:
    id: Optional[int]
    user_id: int
    exam_id: int
    subject_id: Optional[int]
    quiz_type: str
    difficulty: Optional[str]
    num_questions: int
    questions: List[Question]
    start_time: datetime
    end_time: Optional[datetime]
    score: Optional[int]
    time_taken: Optional[int]
    answers: Optional[Dict[str, str]]

    @classmethod
    def create_new(cls, user_id: int, exam_id: int, subject_id: Optional[int], 
                  quiz_type: str, difficulty: Optional[str], num_questions: int) -> 'Quiz':
        return cls(
            id=None,
            user_id=user_id,
            exam_id=exam_id,
            subject_id=subject_id,
            quiz_type=quiz_type,
            difficulty=difficulty,
            num_questions=num_questions,
            questions=[],
            start_time=datetime.now(),
            end_time=None,
            score=None,
            time_taken=None,
            answers=None
        )

    def submit(self, answers: Dict[str, str], time_taken: int) -> None:
        self.answers = answers
        self.time_taken = time_taken
        self.end_time = datetime.now()
        self.score = sum(1 for qid, ans in answers.items() 
                        if self.questions[int(qid)].correct_answer == ans)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'user_id': self.user_id,
            'exam_id': self.exam_id,
            'subject_id': self.subject_id,
            'quiz_type': self.quiz_type,
            'difficulty': self.difficulty,
            'num_questions': self.num_questions,
            'questions': [vars(q) for q in self.questions],
            'start_time': self.start_time.isoformat(),
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'score': self.score,
            'time_taken': self.time_taken,
            'answers': self.answers
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Quiz':
        questions = [Question(**q) for q in data['questions']]
        return cls(
            id=data['id'],
            user_id=data['user_id'],
            exam_id=data['exam_id'],
            subject_id=data['subject_id'],
            quiz_type=data['quiz_type'],
            difficulty=data['difficulty'],
            num_questions=data['num_questions'],
            questions=questions,
            start_time=datetime.fromisoformat(data['start_time']),
            end_time=datetime.fromisoformat(data['end_time']) if data['end_time'] else None,
            score=data['score'],
            time_taken=data['time_taken'],
            answers=data['answers']
        ) 
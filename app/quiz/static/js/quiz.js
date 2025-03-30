// Quiz Timer
class QuizTimer {
    constructor(displayElement, timeInput) {
        this.displayElement = displayElement;
        this.timeInput = timeInput;
        this.startTime = Date.now();
        this.interval = null;
    }

    start() {
        this.update();
        this.interval = setInterval(() => this.update(), 1000);
    }

    stop() {
        if (this.interval) {
            clearInterval(this.interval);
            this.interval = null;
        }
    }

    update() {
        const seconds = Math.floor((Date.now() - this.startTime) / 1000);
        this.timeInput.value = seconds;
        
        const minutes = Math.floor(seconds / 60).toString().padStart(2, '0');
        const remainingSeconds = (seconds % 60).toString().padStart(2, '0');
        this.displayElement.textContent = `${minutes}:${remainingSeconds}`;
    }
}

// Quiz Form Handler
class QuizFormHandler {
    constructor() {
        this.quizType = document.getElementById('quiz_type');
        this.examSelect = document.getElementById('exam_id');
        this.subjectGroup = document.getElementById('subject_group');
        this.subjectSelect = document.getElementById('subject_id');
        this.form = document.getElementById('quizForm');

        if (this.form) {
            this.initializeEventListeners();
        }
    }

    initializeEventListeners() {
        // Quiz type change handler
        this.quizType.addEventListener('change', () => {
            if (this.quizType.value === 'subject_wise') {
                if (this.examSelect.value) {
                    this.updateSubjects(this.examSelect.value);
                } else {
                    this.hideSubjectGroup();
                }
            } else {
                this.hideSubjectGroup();
            }
        });

        // Exam selection change handler
        this.examSelect.addEventListener('change', () => {
            if (this.quizType.value === 'subject_wise') {
                this.updateSubjects(this.examSelect.value);
            }
        });

        // Form submission handler
        this.form.addEventListener('submit', async (e) => {
            e.preventDefault();
            await this.handleSubmit();
        });
    }

    hideSubjectGroup() {
        this.subjectGroup.style.display = 'none';
        this.subjectSelect.value = '';
    }

    async updateSubjects(examId) {
        try {
            const response = await fetch(`/quiz/api/subjects?exam_id=${examId}`);
            if (!response.ok) throw new Error('Failed to fetch subjects');
            
            const subjects = await response.json();
            this.subjectSelect.innerHTML = '<option value="">Choose a subject</option>';
            subjects.forEach(subject => {
                this.subjectSelect.innerHTML += `<option value="${subject.id}">${subject.name}</option>`;
            });
            this.subjectGroup.style.display = 'block';
        } catch (error) {
            console.error('Error fetching subjects:', error);
            this.hideSubjectGroup();
        }
    }

    async handleSubmit() {
        try {
            const formData = new FormData(this.form);
            const response = await fetch(this.form.action, {
                method: 'POST',
                body: formData,
                headers: {
                    'X-Requested-With': 'XMLHttpRequest'
                }
            });

            if (!response.ok) {
                throw new Error('Network response was not ok');
            }

            const html = await response.text();
            const temp = document.createElement('div');
            temp.innerHTML = html;
            
            const questionsContainer = temp.querySelector('#questions-container');
            if (questionsContainer) {
                this.form.parentElement.innerHTML = questionsContainer.outerHTML;
                this.initializeQuizTimer();
                questionsContainer.scrollIntoView({ behavior: 'smooth' });
            } else {
                window.location.reload();
            }
        } catch (error) {
            console.error('Error submitting form:', error);
            window.location.reload();
        }
    }

    initializeQuizTimer() {
        const timerElement = document.getElementById('timer-display');
        const timeInput = document.getElementById('time_taken');
        if (timerElement && timeInput) {
            const timer = new QuizTimer(timerElement, timeInput);
            timer.start();
        }
    }
}

// Question Review Handler
class QuestionReviewHandler {
    constructor() {
        this.initializeStarRating();
    }

    initializeStarRating() {
        const ratingInputs = document.querySelectorAll('input[name="rating"]');
        ratingInputs.forEach(input => {
            input.addEventListener('change', (e) => {
                const stars = e.target.closest('form').querySelectorAll('svg');
                stars.forEach((star, index) => {
                    if (index < parseInt(e.target.value)) {
                        star.classList.add('text-yellow-400');
                        star.classList.remove('text-gray-300');
                    } else {
                        star.classList.remove('text-yellow-400');
                        star.classList.add('text-gray-300');
                    }
                });
            });
        });
    }
}

// Initialize components when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    new QuizFormHandler();
    new QuestionReviewHandler();
}); 
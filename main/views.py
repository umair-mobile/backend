from rest_framework import viewsets, permissions
from .models import ClassRoom, Assignment, Submission
from .serizalizer import ClassRoomSerializer, AssignmentSerializer, SubmissionSerializer
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status
import os
import fitz
import docx
from sentence_transformers import SentenceTransformer, util
import chardet
from django.conf import settings


model = SentenceTransformer('D:\\Project\\stsb-roberta-large1')

def extract_text(file_path):
    try:
        if file_path.endswith(".pdf"):
            doc = fitz.open(file_path)
            return "\n".join(page.get_text("text") for page in doc)
        elif file_path.endswith(".docx"):
            doc = docx.Document(file_path)
            return "\n".join(para.text for para in doc.paragraphs)
        elif file_path.endswith(".txt"):
            with open(file_path, "rb") as f:
                raw_data = f.read()
                encoding = chardet.detect(raw_data)['encoding'] or 'utf-8'
                return raw_data.decode(encoding, errors='ignore')
        else:
            return ""
    except Exception as e:
        print(f"Error extracting text from {file_path}: {e}")
        return ""

def evaluate_submission(student_text, correct_embedding, min_words, required_keywords, max_marks):
    try:
        word_count = len(student_text.split())
        if word_count < min_words:
            return 0, f"0% semantically similar (Too short: {word_count} words < {min_words})"

        
        student_embedding = model.encode(student_text, convert_to_tensor=True)
        similarity = util.cos_sim(correct_embedding, student_embedding).item()

    
        kw_score = sum(kw.lower() in student_text.lower() for kw in required_keywords) / len(required_keywords)

        
        marks = round((similarity * 0.9 + kw_score * 0.1) * max_marks, 2)
        if similarity < 0.30:
            # Reduce marks more aggressively, e.g., scale down to make marks "more low"
            marks = marks * 0.4  # Scale down by 70% when similarity > 30%

        marks = round(marks, 2)
        # Generate similarity text
        sim_text = f"{round(similarity * 100, 2)}% semantically similar"
        if similarity > 0.80:
            sim_text += " ⚠️ Possible copy"
        if word_count < min_words:
            sim_text += " (Too short)"

        return marks, sim_text
    except Exception as e:
        print(f"Error evaluating submission: {e}")
        return 0, f"Error: {e}"
    
    

def check_plagiarism(student_embeddings, submission_id_to_student):
    plagiarism_results = []
    student_files = list(student_embeddings.keys())
    for i, file1 in enumerate(student_files):
        for file2 in student_files[i + 1:]:
            sim = util.cos_sim(student_embeddings[file1], student_embeddings[file2]).item()
            if sim > 0.80:
                student1_name = submission_id_to_student.get(file1, "Unknown")
                student2_name = submission_id_to_student.get(file2, "Unknown")
                plagiarism_results.append((student1_name, student2_name, round(sim * 100, 2)))
    return plagiarism_results

class ClassRoomViewSet(viewsets.ModelViewSet):
    queryset = ClassRoom.objects.all()
    serializer_class = ClassRoomSerializer
    permission_classes = [permissions.IsAuthenticated]
    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
    @action(detail=False, methods=['post'], url_path='join')
    def join_classroom(self, request):
        code = request.data.get('code')
        if not code:
            return Response({'error': 'Classroom code is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            classroom = ClassRoom.objects.get(code=code)
        except ClassRoom.DoesNotExist:
            return Response({'error': 'Classroom not found.'}, status=status.HTTP_404_NOT_FOUND)

        user = request.user
        classroom.students.add(user)
        return Response({'message': f'Joined classroom "{classroom.name}" successfully.'})
    @action(detail=False, methods=['get'], url_path='my-classes')
    def my_classes(self, request):
        user = request.user
        created_classes = ClassRoom.objects.filter(created_by=user)
        joined_classes = ClassRoom.objects.filter(students=user).exclude(created_by=user)

        created_serializer = self.get_serializer(created_classes, many=True)
        joined_serializer = self.get_serializer(joined_classes, many=True)

        return Response({
            'created_classes': created_serializer.data,
            'joined_classes': joined_serializer.data
        })
    @action(detail=True, methods=['get', 'post'], url_path='assignments')
    def assignments(self, request, pk=None):
        try:
            classroom = ClassRoom.objects.get(id=pk)
        except ClassRoom.DoesNotExist:
            return Response({"error": "Classroom not found."}, status=status.HTTP_404_NOT_FOUND)

        # Only teacher (creator) or students who joined can get assignments
        if request.method == "GET":
            user = request.user
            if user != classroom.created_by and not classroom.students.filter(id=user.id).exists():
                return Response({"error": "You are not part of this classroom."}, status=status.HTTP_403_FORBIDDEN)
            assignments = Assignment.objects.filter(classroom=classroom)
            serializer = AssignmentSerializer(assignments, many=True)
            return Response({"assignments": serializer.data})

        # Only teacher can create assignments for this classroom
        if request.method == "POST":
            if request.user != classroom.created_by:
                return Response({"error": "Only teacher can add assignments."}, status=status.HTTP_403_FORBIDDEN)
            serializer = AssignmentSerializer(data=request.data)
            if serializer.is_valid():
                serializer.save(classroom=classroom)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            else:
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class AssignmentViewSet(viewsets.ModelViewSet):
    queryset = Assignment.objects.all()
    serializer_class = AssignmentSerializer
    permission_classes = [permissions.IsAuthenticated]

    """ def perform_create(self, serializer):
        classroom_id = self.request.data.get('classroom')
        classroom = ClassRoom.objects.get(id=classroom_id)

        # Only allow the teacher (classroom creator) to add
        if self.request.user != classroom.created_by:
            raise PermissionError("You are not allowed to assign assignments to this classroom.")

        serializer.save(classroom=classroom)

    @action(detail=False, methods=['get'], url_path='by-class/(?P<class_id>[^/.]+)')
    def assignments_by_class(self, request, class_id=None):
        user = request.user
        try:
            classroom = ClassRoom.objects.get(id=class_id)
        except ClassRoom.DoesNotExist:
            return Response({"error": "Class not found"}, status=status.HTTP_404_NOT_FOUND)

        if user != classroom.created_by and not classroom.students.filter(id=user.id).exists():
            return Response({"error": "You are not part of this classroom"}, status=status.HTTP_403_FORBIDDEN)

        assignments = Assignment.objects.filter(classroom=classroom)
        serializer = self.get_serializer(assignments, many=True)
        return Response(serializer.data)"""

class SubmissionViewSet(viewsets.ModelViewSet):
    queryset = Submission.objects.all()
    serializer_class = SubmissionSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def perform_create(self, serializer):
        # Automatically set the student to the authenticated user
        submission = serializer.save(student=self.request.user)
        
        try:
            # Fetch the assignment
            assignment = submission.assignment
            print(assignment)
            max_marks = assignment.max_marks 
            min_words = assignment.min_words 
            required_keywords = assignment.required_keywords or ["AI", "making decisions", "recognizing patterns"]

            # Get the teacher's assignment file path
            correct_file_path = os.path.join(settings.MEDIA_ROOT, assignment.file.name)
            if not os.path.exists(correct_file_path):
                print(f"Error: Teacher file {correct_file_path} not found")
                return

            # Extract text and compute embedding for the teacher's file
            correct_text = extract_text(correct_file_path)
            if not correct_text:
                print(f"Error: Could not extract text from teacher file {correct_file_path}")
                return
            correct_embedding = model.encode(correct_text, convert_to_tensor=True)

            # Get the student's submitted file path
            if not submission.submitted_file:
                print(f"Warning: No file for submission {submission.id}")
                return
            student_file_path = os.path.join(settings.MEDIA_ROOT, submission.submitted_file.name)
            if not os.path.exists(student_file_path):
                print(f"Warning: Submission file {student_file_path} not found")
                return

            # Extract text from student's submission
            student_text = extract_text(student_file_path)
            if not student_text:
                print(f"Warning: Could not extract text from {student_file_path}")
                return

            # Evaluate the submission
            marks, feedback = evaluate_submission(student_text, correct_embedding, min_words, required_keywords, max_marks)

            # Check for plagiarism with other submissions for this assignment
            student_embeddings = {submission.id: model.encode(student_text, convert_to_tensor=True)}
            submission_id_to_student = {submission.id: submission.student.name}
            other_submissions = Submission.objects.filter(assignment=assignment).exclude(id=submission.id)
            for other in other_submissions:
                if other.submitted_file:
                    other_file_path = os.path.join(settings.MEDIA_ROOT, other.submitted_file.name)
                    if os.path.exists(other_file_path):
                        other_text = extract_text(other_file_path)
                        if other_text:
                            student_embeddings[other.id] = model.encode(other_text, convert_to_tensor=True)
                            submission_id_to_student[other.id] = other.student.name

            plagiarism_results = check_plagiarism(student_embeddings, submission_id_to_student)
            if plagiarism_results:
                print("\n🔍 Plagiarism Check Between Students (Similarity > 80%):\n" + "-" * 50)
                plagiarism_feedback = []
                for student1, student2, sim in plagiarism_results:
                    print(f"{student1} <-> {student2}: {sim}% similar")
                    if submission.student.name in (student1, student2):
                        other_student = student2 if student1 == submission.student.name else student1
                        plagiarism_feedback.append(f"{other_student} same {sim}%")
                if plagiarism_feedback:
                    feedback += " | Plagiarism: " + ", ".join(plagiarism_feedback)

            # Update the submission with marks and feedback
            submission.marks = marks
            submission.feedback = feedback
            submission.save()
            print(f"Updated submission {submission.id} - Marks: {marks}, Feedback: {feedback}")

        except Assignment.DoesNotExist:
            print(f"Error: Assignment with ID {submission.assignment_id} not found")
        except Exception as e:
            print(f"Error processing submission {submission.id}: {e}")

    def get_queryset(self):
        assignment_id = self.request.query_params.get('assignment_id')
        if not assignment_id:
            return Submission.objects.none()  # Return empty if no assignment_id

        try:
            assignment = Assignment.objects.get(id=assignment_id)
            classroom = assignment.classroom
            user = self.request.user

            # Teachers (creators) see all submissions for the assignment
            if user == classroom.created_by:
                return Submission.objects.filter(assignment_id=assignment_id)
            # Students see only their own submissions
            elif classroom.students.filter(id=user.id).exists():
                return Submission.objects.filter(assignment_id=assignment_id, student=user)
            else:
                return Submission.objects.none()  # Not part of classroom
        except Assignment.DoesNotExist:
            print(f"Error: Assignment with ID {assignment_id} not found")
            return Submission.objects.none()

    @action(detail=True, methods=['patch'], url_path='grade')
    def grade_submission(self, request, pk=None):
        try:
            submission = self.get_object()
            assignment = submission.assignment
            if request.user != assignment.classroom.created_by:
                return Response({"error": "Only the teacher can grade submissions."}, status=status.HTTP_403_FORBIDDEN)
            
            marks = request.data.get('marks')
            feedback = request.data.get('feedback')
            
            if marks is not None:
                submission.marks = float(marks)
            if feedback is not None:
                submission.feedback = feedback
            submission.save()
            
            serializer = self.get_serializer(submission)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": f"Error grading submission: {e}"}, status=status.HTTP_400_BAD_REQUEST)
        

class Update(viewsets.ModelViewSet):
    queryset = Submission.objects.all()
    serializer_class = SubmissionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def update(self, request, *args, **kwargs):
        try:
            submission = self.get_object()
            assignment = submission.assignment
            if request.user != assignment.classroom.created_by:
                return Response({"error": "Only the teacher can update submissions."}, status=status.HTTP_403_FORBIDDEN)

            # Update fields from request data
            submission.marks = request.data.get('marks', submission.marks)
            submission.feedback = request.data.get('feedback', submission.feedback)
            submission.save()

            serializer = self.get_serializer(submission)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
                return Response({"error": f"Error updating submission: {e}"}, status=status.HTTP_400_BAD_REQUEST)
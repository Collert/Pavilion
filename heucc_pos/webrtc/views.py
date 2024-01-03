from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .models import WebRTCSession

# Create your views here.

@csrf_exempt
def post_offer(request):
    if request.method == 'POST':
        offer = request.POST.get('offer')
        session = WebRTCSession(offer=offer)
        session.save()
        return JsonResponse({'session_id': session.session_id})
    elif request.method == "GET":
        return render(request, "webrtc/laptop.html")

@csrf_exempt
def get_answer(request, session_id):
    try:
        session = WebRTCSession.objects.get(session_id=session_id)
        return JsonResponse({'answer': session.answer})
    except WebRTCSession.DoesNotExist:
        return JsonResponse({'error': 'Session not found'}, status=404)

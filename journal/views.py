from django.shortcuts import render, redirect
from django.contrib import messages
from .models import CallForPaper, Release, AboutUsConfig
from .forms import SubmissionForm
from django.utils import timezone

def journal_home(request):
    # Get active call for papers
    now = timezone.now()
    active_call = CallForPaper.objects.filter(is_active=True, start_date__lte=now, end_date__gte=now).first()
    
    # Get releases
    releases = Release.objects.all()
    
    # Get About Us config
    about_config = AboutUsConfig.objects.first()
    about_data = about_config.data if about_config else {"description": "", "members": []}

    # Handle Submission form
    if request.method == 'POST':
        if not active_call:
            messages.error(request, "در حال حاضر فراخوانی برای ارسال مقاله فعال نیست.")
            return redirect('journal_home')
            
        form = SubmissionForm(request.POST, request.FILES)
        if form.is_valid():
            submission = form.save(commit=False)
            submission.call = active_call
            submission.save()
            messages.success(request, "مقاله شما با موفقیت ثبت شد.")
            return redirect('journal_home')
        else:
            messages.error(request, "خطایی در فرم وجود دارد. لطفا فیلدها را بررسی کنید.")
    else:
        form = SubmissionForm()

    context = {
        'active_call': active_call,
        'releases': releases,
        'about_data': about_data,
        'form': form,
    }
    return render(request, 'journal/home.html', context)
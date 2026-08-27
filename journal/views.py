from django.shortcuts import render, redirect
from django.contrib import messages
from django.utils import timezone
from .models import CallForPaper, Release, AboutUsConfig
from .forms import SubmissionForm

def journal_home(request):
    releases = Release.objects.all()
    about_config = AboutUsConfig.objects.first()
    about_data = about_config.data if about_config else {"description": "", "members": []}
    
    now = timezone.now()
    has_active_call = CallForPaper.objects.filter(is_active=True, start_date__lte=now, end_date__gte=now).exists()

    context = {
        'releases': releases,
        'about_data': about_data,
        'has_active_call': has_active_call,
    }
    return render(request, 'journal/home.html', context)

def journal_submit(request):
    now = timezone.now()
    active_call = CallForPaper.objects.filter(is_active=True, start_date__lte=now, end_date__gte=now).first()

    if not active_call:
        messages.error(request, "در حال حاضر فراخوانی برای ارسال مقاله فعال نیست.")
        return redirect('journal_home')

    if request.method == 'POST':
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

    return render(request, 'journal/submit.html', {'form': form, 'active_call': active_call})
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from .models import CallForPaper, Release, AboutUsConfig
from .forms import SubmissionForm

def journal_home(request):
    releases = Release.objects.all()
    now = timezone.now()
    active_calls = CallForPaper.objects.filter(
        is_active=True, start_date__lte=now, end_date__gte=now
    ).order_by('end_date')

    context = {
        'releases': releases,
        'active_calls': active_calls,
    }
    return render(request, 'journal/home.html', context)

def journal_about(request):
    about_config = AboutUsConfig.objects.first()
    raw_about_data = about_config.data if about_config and about_config.data else {}
    
    about_data = {
        "description": raw_about_data.get("description", ""),
        "members": raw_about_data.get("members", [])
    }
    
    return render(request, 'journal/about.html', {'about_data': about_data})

def journal_submit(request, call_id):
    now = timezone.now()
    active_call = get_object_or_404(CallForPaper, id=call_id)

    if not active_call.is_open:
        messages.error(request, "زمان ارسال مقاله برای این فراخوان به پایان رسیده یا غیرفعال است.")
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

def journal_release_detail(request, release_id):
    release = get_object_or_404(Release, id=release_id)
    return render(request, 'journal/release_detail.html', {'release': release})
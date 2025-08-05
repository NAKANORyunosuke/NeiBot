from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from bot.bot_client import bot, send_message_to_channel


test_channel_id = 1401953150558277795


@csrf_exempt
def home(request):
    if request.method == 'POST':
        if "send_message" in request.POST:
            print("送信ボタンが押されました")

            loop = getattr(bot, 'loop', None)
            if loop and loop.is_running():
                loop.create_task(send_message_to_channel(test_channel_id, "Webから送信されました"))
            else:
                print("Botが未起動または準備中")
            return render(request, 'main/home.html', {'message': '送信処理しました'})
    return render(request, 'main/home.html')
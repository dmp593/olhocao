document.addEventListener('DOMContentLoaded', function() {
    const messages = document.querySelectorAll('#messages-container > div');
    
    messages.forEach(message => {
        setTimeout(() => {
            message.classList.remove('animate__fadeInRight');
            message.classList.add('animate__fadeOutRight');
            setTimeout(() => message.remove(), 300);
        }, 5000);
    });
});

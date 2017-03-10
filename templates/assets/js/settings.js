// Settings Page
$(function(){
    $('#radarr_check').on('click', function() {
        alite({
            url: '/radarr/check',
            method: 'POST',
            data: {
                url: $("#radarr_url").val().trim(),
                apikey: $("#radarr_apikey").val().trim()
            },
        }).then(function (result) {
            console.log('radarr_check result: ', result);
            if (result == 'ERR') {
                // apikey was invalid
                $("#radarr_check").removeClass("btn-success").addClass("btn-danger");
            } else {
                // apikey was valid
                $("#radarr_check").removeClass("btn-danger").addClass("btn-success");
            }
        }).catch(function (err) {
            console.error('radarr_check error: ', err);
            $("#radarr_check").removeClass("btn-success").addClass("btn-danger");
        });        
    });
});
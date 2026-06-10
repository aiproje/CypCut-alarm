// Delay loading any function until the html dom has loaded. All functions are
// defined in this top level function to ensure private scope.
$(document).ready(function () {

    // Installs error handling.
    $.ajaxSetup({
        error: function (resp, e) {
            if (resp.status == 0) {
                alert('You are offline!!\n Please Check Your Network.');
            } else if (resp.status == 404) {
                alert('Requested URL not found.');
            } else if (resp.status == 500) {
                alert('Internel Server Error:\n\t' + resp.responseText);
            } else if (e == 'parsererror') {
                alert('Error.\nParsing JSON Request failed.');
            } else if (e == 'timeout') {
                alert('Request timeout.');
            } else {
                alert('Unknown Error.\n' + resp.responseText);
            }
        }
    });  // error:function()


    var generate_btn = $('#generate_btn');
    var sample_1_btn = $('#sample_1_btn');
    var sample_2_btn = $('#sample_2_btn');
    var sample_3_btn = $('#sample_3_btn');
    var sample_4_btn = $('#sample_4_btn');
    var sample_5_btn = $('#sample_5_btn');

    var svg_div = $('#graphviz_svg_div');
    var graphviz_data_textarea = $('#graphviz_data');

    $(document).ready(function () {
        $('#file_input').on('change', function (event) {
            var file = event.target.files[0];
            if (file) {
                var reader = new FileReader();
                reader.onload = function (e) {
                    var content = e.target.result;
                    console.log(content);
                    svg_div.html("");
                    InsertGraphvizText(content);
                    UpdateGraphviz(content);
                };
                reader.readAsText(file);
            }
        });
    });

    function InsertGraphvizText(text) {
        $('#graphviz_data').html(text);
    }

    function UpdateGraphviz(hoverData) {
        var content = $('#graphviz_data').val();
        if (content.indexOf("digraph") < 0) {
            content = "digraph G {" + content + "}";
        }
        Viz.instance().then(function (viz) {
            var svg = viz.renderSVGElement(content);
            //console.log(svg)
            //console.log(svg.outerHTML)
            //$('#graphviz_svg_div').html("<hr>" + svg.outerHTML);
            $('#graphviz_svg_div').html("");
            document.getElementById("graphviz_svg_div").appendChild(svg);
            addHover(hoverData);
        });
        //var svg = Viz(content, "svg");
        //console.log("test---------------------------")
        //console.log(svg)
        //$('#graphviz_svg_div').html("<hr>" + svg);
        //addHover();
    }

    function addHover(hoverData) {
        if (hoverData != "") {
            console.log(hoverData)
            d3.selectAll('g.node').each(function () {
                d3.select(this).attr("cursor", "pointer");
                let nodeId = d3.select(this).select('title').text();
                
                //console.log(nodeId)
                d3.select(this).select("title")
                    //.attr("class", "hover-title")
                    .html(hoverData[nodeId]);
                let folderNode = d3.select(this).select("a");
                folderNode.attr("xlink:title", hoverData[nodeId])
                folderNode.attr("target", "_blank")
            })
        }

        // Add hover event listeners to nodes
        d3.selectAll('g.node')
            .on("mouseover", function (event, d) {
                // Highlight the hovered node
                d3.select(this).select('ellipse').classed('hovered-node', true);
            })
            .on("mouseout", function (event, d) {
                // Remove highlight from the hovered node
                d3.select(this).select('ellipse').classed('hovered-node', false);
                d3.selectAll('g.node').select('ellipse').classed('hovered-node-child', false);
                d3.selectAll('g.node').select('ellipse').classed('hovered-node-parent', false);

                // Remove highlight from related edges
                d3.selectAll('g.edge').select('path').classed('hovered-edge-in', false);
                d3.selectAll('g.edge').select('path').classed('hovered-edge-out', false);
            });
    }

    // Startup function: call UpdateGraphviz
    jQuery(function () {
        // The buttons are disabled, enable them now that this script
        // has loaded.
        generate_btn.removeAttr("disabled")
            .text("Generate Graph!");

        sample_1_btn.removeAttr("disabled");
        sample_2_btn.removeAttr("disabled");
        sample_3_btn.removeAttr("disabled");
        sample_4_btn.removeAttr("disabled");
        sample_5_btn.removeAttr("disabled");
    });

    // Bind actions to form buttons.
    generate_btn.click(function () {
        UpdateGraphviz("")
    });

    sample_1_btn.click(function () {
        InsertGraphvizText(jQuery("#sample1_text").html().trim());
    });

    sample_2_btn.click(function () {
        InsertGraphvizText(jQuery("#sample2_text").html().trim());
    });

    sample_3_btn.click(function () {
        InsertGraphvizText(jQuery("#sample3_text").html().trim());
    });

    sample_4_btn.click(function () {
        InsertGraphvizText(jQuery("#sample4_text").html().trim());
    });

    sample_5_btn.click(function () {
        InsertGraphvizText(jQuery("#sample5_text").html().trim());
    });

    function UpdateWorkLogGraphviz() {
        console.log($("#start-time-input").val())
        console.log($("#end-time-input").val())
        $.ajax({
            url: '/getLogGV',
            type: 'GET',
            dataType: 'text',
            "data": {
                "StartTime": $("#start-time-input").val(),
                "EndTime": $("#end-time-input").val()
            },
            responseType: 'json',
            success: function (resp) {
                svg_div.html("");
                var respJ = JSON.parse(resp);
                console.log(respJ);
                InsertGraphvizText(respJ.data);
                UpdateGraphviz(respJ.hover);
                //setTimeout(function () {
                //    UpdateWorkLogGraphviz();
                //}, 1000)
            },
            error: function (resp, e) {
                console.log(e)
                svg_div.html("");
                InsertGraphvizText(JSON.parse(resp).data);
                UpdateGraphviz("");
            }
        })
    }
	
    $('#sample_6_btn').click(function () { 
        UpdateWorkLogGraphviz();
	})

});

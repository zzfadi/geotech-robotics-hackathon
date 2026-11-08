"""Execute the actual UI formatting functions; no server or inference calls."""
import json
from pathlib import Path
import unittest
try:
    import quickjs
except ImportError:
    quickjs=None

@unittest.skipIf(quickjs is None,'Run with uv --with quickjs for JavaScript UI tests')
class WebCupStatus(unittest.TestCase):
    def setUp(self):
        html=(Path(__file__).parent/'web/index.html').read_text()
        quickjs.Context().eval('new Function('+json.dumps(html.split('<script>')[1].split('</script>')[0])+')')
        start=html.index('function cupRecording(');end=html.index('function retryFleetRecording',start)
        self.js=quickjs.Context();self.js.eval("let fleetActive=false; const tkey=i=>'T'+String(i).padStart(2,'0');"+html[start:end])
    def evaluate(self,script):return json.loads(self.js.eval('JSON.stringify('+script+')'))
    def test_all_cups_and_transient_peak_not_grasp(self):
        r={'cup_metrics':{'MakerStation_Prop_Cup':{'height_cm':.03,'held_slow_above_10cm_s':0,'max_height_cm':.04,'fresh':True},'MakerStation_Prop_Cup_03':{'height_cm':-3,'held_slow_above_10cm_s':0,'max_height_cm':58.7,'fresh':True}},'outcome':'unverified','table':'T00','calls':24}
        d={'recording':{'tables':[0],'results':[r]},'command':{'status':'done','tables':[0]}}
        value=self.evaluate('tableStatus({index:0},'+json.dumps(d)+')')
        self.assertIn('Cup_03: rise -3.0 cm',value[0]);self.assertIn('58.7 cm (not grasp proof)',value[0])
        self.assertIn('Run finished · pickup unverified',value[0]);self.assertEqual(value[1],'')
    def test_recording_not_filtered_to_old_target(self):
        self.assertTrue(self.evaluate("cupRecording({results:[{table:'T00',verification:{target:'MakerStation_Prop_Cup_03'}}]},0)"))
    def test_authoritative_command_clears_stale_receipt(self):
        self.js.eval("let e={textContent:'Accepted: old',dataset:{receiptId:'old'}};clearStaleReceipt(e,{id:'new',status:'running'});")
        self.assertEqual(self.evaluate('e.textContent'),'')
        self.js.eval("e={textContent:'Accepted: new',dataset:{receiptId:'new'}};clearStaleReceipt(e,{id:'new',status:'running'});")
        self.assertEqual(self.evaluate('e.textContent'),'Accepted: new')
        self.js.eval("clearStaleReceipt(e,{id:'new',status:'done'});")
        self.assertEqual(self.evaluate('e.textContent'),'')

if __name__=='__main__':unittest.main()

# Issue #150 reproducer (synthetic)
Run: python runner/phases/m1_multi_output.py --repo .
nine_out.onnx: 9 outputs, offsets +1..+9. two_out_same_shape.onnx: A=X+1,B=X+10.
A mismatch by name (A matching B's values) indicates output misrouting.
Expected/actual JSON under reports/m1/mismatch/ when a failure occurs.

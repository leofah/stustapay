package de.stustanet.stustapay.ui.debug

import androidx.compose.foundation.layout.*
import androidx.compose.material.Button
import androidx.compose.material.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.navigation.NavHostController
import de.stustanet.stustapay.ui.nav.navigateTo

@Composable
fun DebugNavView(nav: NavHostController) {
    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Text(text = "Debug Tools", fontSize = 24.sp)
        Spacer(modifier = Modifier.height(16.dp))

        Button(
            modifier = Modifier.fillMaxWidth().padding(8.dp),
            onClick = {
                nav.navigateTo("net")
            }
        ) {
            Text( "Network", fontSize = 24.sp)
        }

        Button(
            modifier = Modifier.fillMaxWidth().padding(8.dp),
            onClick = {
                nav.navigateTo("nfc")
            }
        ) {
            Text( "NFC", fontSize = 24.sp)
        }
    }
}